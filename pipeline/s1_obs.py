# -*- coding: utf-8 -*-
"""
พื้นที่น้ำท่วมจาก Sentinel-1 (SAR) โดยไม่ต้องใช้ GISTDA API Key → "สัดส่วนท่วมราย hex" สำหรับสอบเทียบโมเดล

แหล่งภาพ: Microsoft Planetary Computer, collection `sentinel-1-rtc` (gamma0 ที่แก้ terrain แล้ว, COG, 10 ม.)
          อ่านได้แบบ anonymous (planetary_computer.sign ขอ SAS token ให้เอง) ไม่ต้องสมัครหรือใช้ key
          หรือใช้ภาพที่ประมวลผลเองใน SNAP / ArcGIS Pro (SAR toolset) ผ่าน --local-flood / --local-ref

วิธีแปล (ต่อหนึ่ง "การผ่าน" = ภาพทุก frame ของ orbit เดียวกันที่ถ่ายห่างกัน < 10 นาที)
  1. mosaic VV ลงกริด UTM 47N ความละเอียด RES_M (average บนค่า linear → ลด speckle ไปด้วย) แล้วแปลงเป็น dB
  2. ภาพอ้างอิง = median ของ VV ใน relative orbit เดียวกัน ช่วง REF_DAYS ก่อนวันถ่าย
     (ไม่มี → ช่วงเดียวกันของปีก่อน ±REF_LASTYEAR_D วัน ; ยังไม่มีอีก → ไม่สร้างระเบียน เพราะแยกนาข้าวที่มีน้ำขังออกไม่ได้)
  3. น้ำ = VV_dB < threshold (Otsu บน histogram ของพื้นที่ที่ใช้ได้, จำกัดไว้ THR_CLIP)
     น้ำท่วม = น้ำ และ (VV_dB − VV_ref_dB) < DIFF_DB  → ตัดแหล่งน้ำถาวรและนาที่ขังน้ำอยู่แล้วตั้งแต่ก่อนเหตุการณ์
  4. กรอง speckle ด้วย majority 3×3 แล้วนับพิกเซลเข้า hex ที่ใกล้ที่สุด → สัดส่วนท่วมราย hex
  5. hex ที่ SAR เชื่อถือไม่ได้ → ใส่ใน `nocov` (ไม่นับทั้งสองฝั่งตอนสอบเทียบ):
       ภาพครอบคลุม < COV_MIN, HAND_P10 > HAND_MAX (เนิน/เขา: เงาเรดาร์ดูเหมือนน้ำ),
       slope > SLOPE_MAX %, เมือง f_built > BUILT_MAX (น้ำท่วมในเมืองเกิด double bounce → SAR มองไม่เห็น)

ข้อจำกัดที่ควรรู้
  - Sentinel-1 ผ่านพื้นที่เดิมทุก ~6–12 วัน (ไม่ใช่รายวันแบบ GISTDA) แต่รู้เวลาถ่ายแน่นอน → เทียบกับโมเดลในหน้าต่างแคบ WIN_H ชม.
  - น้ำท่วมใต้ต้นไม้/พืชสูง และน้ำท่วมที่มีอยู่แล้วในภาพอ้างอิง (ท่วมนานกว่า REF_DAYS[0] วัน) จะตรวจไม่พบ → POD ต่ำกว่าจริง
  - "ไม่พบน้ำท่วม" ในระเบียนที่ท่วมรวม < MIN_KM2 ไม่ใช้สอบเทียบ (เหมือน GISTDA)

ไฟล์ผลลัพธ์
  data/live/s1_obs.json       ระเบียนรูปแบบเดียวกับ gistda_obs.json (+ t_acq, nocov, thr_db, ref_*) — calibrate.py อ่านรวมกัน
  data/live/s1_flood.geojson  hex ที่ท่วม ≥ 10% ของการผ่านล่าสุด สำหรับแสดงบนเว็บ
  data/live/s1_status.json    สถานะรอบล่าสุด

การใช้งาน
  pip install numpy scipy rasterio pyproj pystac-client planetary-computer
  python pipeline/s1_obs.py --site .                  # ค้นภาพ LOOKBACK_D วันล่าสุด ประมวลผลเฉพาะการผ่านที่ยังไม่เคยทำ
  python pipeline/s1_obs.py --site . --days 45        # ย้อนหลังไกลขึ้น (ครั้งแรก)
  python pipeline/s1_obs.py --site . --local-flood s1_20260925_vv.tif --local-ref s1_20260901_vv.tif --time 2026-09-25T06:10+07:00
      # ภาพที่ทำ terrain correction เองแล้ว (SNAP / ArcGIS Pro) ค่าเป็น linear หรือ dB ก็ได้ CRS ใดก็ได้
"""
import argparse, json, math, os, sys, time
from datetime import datetime, timezone, timedelta
import numpy as np

TZ = timezone(timedelta(hours=7))
PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-1-rtc"
DST_EPSG = 32647          # UTM 47N (นครสวรรค์)
RES_M = 40.0              # ความละเอียดกริดวิเคราะห์ (hex 1 กม² ≈ 625 พิกเซล)
LOOKBACK_D = 14           # ค่าเริ่มต้นของช่วงค้นภาพย้อนหลัง
REF_DAYS = (12, 60)       # ภาพอ้างอิง: 12–60 วันก่อนวันถ่าย
REF_LASTYEAR_D = 30       # สำรอง: ช่วงเดียวกันของปีก่อน ± วัน
REF_MAX_ITEMS = 12        # จำกัดจำนวนภาพที่ใช้ทำ median อ้างอิง
DIFF_DB = -3.0            # ค่าลดลงของ backscatter ที่ถือว่าเป็นน้ำใหม่
THR_CLIP = (-20.0, -13.0) # ขอบเขตของ threshold จาก Otsu (dB)
THR_DEFAULT = -16.0       # ใช้เมื่อพิกเซลที่ใช้ได้น้อยเกินไปจะหา Otsu
HAND_MAX = 15.0           # ม. (hand_p10 ของ hex)
SLOPE_MAX = 8.0           # % (≈ 4.6°)
BUILT_MAX = 0.30          # สัดส่วนพื้นที่เมืองใน hex
COV_MIN = 0.5             # สัดส่วนพื้นที่ hex ที่ต้องมีภาพ
WIN_H = 6                 # หน้าต่างเทียบโมเดล: WIN_H ชม. ก่อนถึงชั่วโมงที่ถ่าย (ถ่ายเวลาแน่นอน ต่างจาก GISTDA รายวัน)
MAX_RECORDS = 120
PASS_GAP_S = 600          # ภาพ orbit เดียวกันที่ห่างกัน < 10 นาที = การผ่านเดียวกัน (หลาย frame)


# ---------------------------------------------------------------- กริดและ hex
class Grid:
    """กริดวิเคราะห์ UTM ครอบคลุม hex ทั้งหมด + การจับคู่พิกเซล → hex ที่ใกล้ที่สุด"""

    def __init__(self, lon, lat, res=RES_M, epsg=DST_EPSG, pad_deg=0.03):
        from pyproj import Transformer
        from rasterio.transform import from_origin
        self.lon, self.lat = np.asarray(lon, float), np.asarray(lat, float)
        self.res, self.crs = res, f"EPSG:{epsg}"
        tf = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
        self.hx, self.hy = tf.transform(self.lon, self.lat)
        bx, by = tf.transform([self.lon.min() - pad_deg, self.lon.max() + pad_deg, self.lon.min() - pad_deg, self.lon.max() + pad_deg],
                              [self.lat.min() - pad_deg, self.lat.min() - pad_deg, self.lat.max() + pad_deg, self.lat.max() + pad_deg])
        self.x0, self.y1 = math.floor(min(bx) / res) * res, math.ceil(max(by) / res) * res
        self.width = int(math.ceil((max(bx) - self.x0) / res)); self.height = int(math.ceil((self.y1 - min(by)) / res))
        self.transform = from_origin(self.x0, self.y1, res, res)
        self.bounds_ll = (self.lon.min() - pad_deg, self.lat.min() - pad_deg, self.lon.max() + pad_deg, self.lat.max() + pad_deg)
        self._idx = None

    def hex_index(self):
        """(H, W) int32: index hex ที่ใกล้ที่สุดของแต่ละพิกเซล (−1 = นอก tessellation) — คำนวณครั้งเดียว"""
        if self._idx is not None: return self._idx
        from scipy.spatial import cKDTree
        tree = cKDTree(np.c_[self.hx, self.hy])
        xs = self.x0 + (np.arange(self.width) + 0.5) * self.res
        idx = np.empty((self.height, self.width), np.int32)
        step = max(1, 2_000_000 // self.width)
        for r0 in range(0, self.height, step):
            ys = self.y1 - (np.arange(r0, min(r0 + step, self.height)) + 0.5) * self.res
            X, Y = np.meshgrid(xs, ys)
            dist, ii = tree.query(np.c_[X.ravel(), Y.ravel()], distance_upper_bound=750.0)   # > รัศมี hex 1 กม² (~620 ม.)
            ii = np.where(np.isfinite(dist), ii, -1)
            idx[r0:r0 + len(ys)] = ii.reshape(len(ys), self.width)
        self._idx = idx
        return idx

    def count(self, mask):
        """จำนวนพิกเซล True ต่อ hex"""
        idx = self.hex_index(); sel = mask & (idx >= 0)
        return np.bincount(idx[sel], minlength=self.lon.size)


# ---------------------------------------------------------------- อ่านภาพ
def read_to_grid(href, grid, db_in=None):
    """อ่าน raster (URL/ไฟล์) ลงกริดวิเคราะห์ด้วย WarpedVRT (average) → linear power, NaN = ไม่มีข้อมูล"""
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="2"):
        with rasterio.open(href) as src:
            nod = src.nodata
            with WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
                           resampling=Resampling.average, src_nodata=nod, nodata=np.nan, dtype="float32") as vrt:
                a = vrt.read(1, masked=False).astype(np.float32)
    a[~np.isfinite(a)] = np.nan
    if nod is not None and np.isfinite(nod): a[a == nod] = np.nan
    if db_in is None:                                             # เดาว่าเป็น dB หรือ linear จากค่ากลาง
        v = a[np.isfinite(a)]
        db_in = v.size > 0 and float(np.median(v)) < 0
    if db_in:
        a = np.power(10.0, a / 10.0, dtype=np.float32)
    a[a <= 0] = np.nan
    return a


def mosaic(hrefs, grid):
    """หลาย frame ของการผ่านเดียวกัน → ค่าเฉลี่ยในส่วนที่ซ้อนกัน"""
    s = np.zeros((grid.height, grid.width), np.float32); n = np.zeros_like(s)
    for h in hrefs:
        a = read_to_grid(h, grid, db_in=False)
        ok = np.isfinite(a); s[ok] += a[ok]; n[ok] += 1
    out = np.full_like(s, np.nan); ok = n > 0; out[ok] = s[ok] / n[ok]
    return out


def to_db(a):
    with np.errstate(divide="ignore", invalid="ignore"):
        return (10.0 * np.log10(a)).astype(np.float32)


# ---------------------------------------------------------------- จำแนก
def otsu(v, lo=-30.0, hi=5.0, bins=350):
    """threshold ของ Otsu บนค่า dB ; คืน (threshold, η = ความแปรปรวนระหว่างกลุ่ม/ทั้งหมด ใช้ดูคุณภาพ ไม่ใช้ตัดสิน)"""
    v = v[(v > lo) & (v < hi)]
    if v.size < 1000: return THR_DEFAULT, 0.0
    h, e = np.histogram(v, bins=bins, range=(lo, hi)); c = (e[:-1] + e[1:]) / 2
    w0 = np.cumsum(h); w1 = w0[-1] - w0
    m0 = np.cumsum(h * c) / np.maximum(w0, 1); m1 = (np.sum(h * c) - np.cumsum(h * c)) / np.maximum(w1, 1)
    sb = w0 * w1 * (m0 - m1) ** 2
    k = int(np.argmax(sb))
    return float(c[k]), float(sb[k] / (w0[-1] ** 2 * v.var() + 1e-9))


def majority3(m):
    """majority filter 3×3 (≥5 ใน 9) ลด speckle ของ mask"""
    p = np.pad(m.astype(np.uint8), 1)
    s = sum(p[1 + dy:p.shape[0] - 1 + dy, 1 + dx:p.shape[1] - 1 + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    return s >= 5


def reliability_mask(p):
    """hex ที่ SAR แปลน้ำท่วมไม่น่าเชื่อถือ (ไม่นับทั้งสองฝั่งตอนสอบเทียบ)"""
    g = lambda k: np.asarray(p.get(k, [0] * p["n"]), float)
    return (g("hand_p10") > HAND_MAX) | (g("slope") > SLOPE_MAX) | (g("f_built") > BUILT_MAX)


def classify(vv, ref, grid, p, hex_km2=1.0):
    """VV (linear) ของการผ่าน + ภาพอ้างอิง (linear) → (dict ผล, mask น้ำท่วมระดับพิกเซล)"""
    db = to_db(vv); ref_db = to_db(ref)
    valid = np.isfinite(db) & np.isfinite(ref_db)
    idx = grid.hex_index()
    bad_hex = reliability_mask(p)
    good_px = valid & (idx >= 0) & ~bad_hex[np.clip(idx, 0, None)]
    thr, sep = otsu(db[good_px])
    if not np.isfinite(thr): thr = THR_DEFAULT
    thr = float(np.clip(thr, *THR_CLIP))
    with np.errstate(invalid="ignore"):
        flood = valid & (db < thr) & ((db - ref_db) < DIFF_DB)
    flood = majority3(flood) & valid
    cell_km2 = (grid.res / 1000.0) ** 2
    n_valid = grid.count(valid); n_fl = grid.count(flood)
    cov = np.minimum(n_valid * cell_km2 / hex_km2, 1.0)
    fr = np.minimum(n_fl * cell_km2 / hex_km2, 1.0)
    nocov = (cov < COV_MIN) | bad_hex
    fr[nocov] = 0.0
    ids = np.nonzero(fr >= 0.02)[0]
    return {"thr_db": round(thr, 2), "otsu_sep": round(sep, 3), "km2": round(float(fr.sum() * hex_km2), 2),
            "cov_hex": int((cov >= COV_MIN).sum()), "hex": ids.tolist(), "frac": [round(float(fr[i]), 2) for i in ids],
            "nocov": np.nonzero(nocov)[0].tolist()}, flood


# ---------------------------------------------------------------- STAC
def stac_client():
    import pystac_client, planetary_computer
    return pystac_client.Client.open(PC_STAC, modifier=planetary_computer.sign_inplace)


def search(cat, bbox, t0, t1, rel_orbit=None):
    kw = dict(collections=[COLLECTION], bbox=bbox, datetime=f"{t0.isoformat()}/{t1.isoformat()}")
    if rel_orbit is not None: kw["query"] = {"sat:relative_orbit": {"eq": int(rel_orbit)}}
    items = [it for it in cat.search(**kw).items() if "vv" in it.assets]
    items.sort(key=lambda it: it.datetime)
    return items


def group_passes(items):
    """รวม frame ของการผ่านเดียวกัน (platform + relative orbit เดียวกัน, เวลาห่าง < PASS_GAP_S)"""
    out = []
    for it in items:
        k = (it.properties.get("platform"), it.properties.get("sat:relative_orbit"))
        if out and out[-1]["k"] == k and (it.datetime - out[-1]["t1"]).total_seconds() < PASS_GAP_S:
            out[-1]["items"].append(it); out[-1]["t1"] = it.datetime
        else:
            out.append({"k": k, "t0": it.datetime, "t1": it.datetime, "items": [it]})
    for g in out:
        g["t"] = g["t0"] + (g["t1"] - g["t0"]) / 2
        g["key"] = "s1-" + g["t"].astimezone(TZ).strftime("%Y-%m-%dT%H%M") + f"-o{g['k'][1]}"
    return out


def reference(cat, grid, bbox, g):
    """median VV ของ relative orbit เดียวกันก่อนวันถ่าย (สำรอง: ช่วงเดียวกันของปีก่อน)"""
    t, orb = g["t"], g["k"][1]
    tries = [("pre", t - timedelta(days=REF_DAYS[1]), t - timedelta(days=REF_DAYS[0])),
             ("lastyear", t - timedelta(days=365 + REF_LASTYEAR_D), t - timedelta(days=365 - REF_LASTYEAR_D))]
    for mode, a, b in tries:
        passes = group_passes(search(cat, bbox, a, b, orb))[-REF_MAX_ITEMS:]
        if not passes: continue
        stack = [mosaic([it.assets["vv"].href for it in pg["items"]], grid) for pg in passes]
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                ref = np.nanmedian(np.stack(stack), axis=0).astype(np.float32)
        return ref, mode, [pg["t"].astimezone(TZ).strftime("%Y-%m-%d") for pg in passes]
    return None, None, []


# ---------------------------------------------------------------- ที่เก็บ
def _paths(site):
    return os.path.join(site, "data", "live", "s1_obs.json"), os.path.join(site, "data", "static", "s1_obs.json")


def load(site):
    for pth in _paths(site):
        if os.path.exists(pth):
            try:
                js = json.load(open(pth, encoding="utf8"))
                if isinstance(js, dict) and isinstance(js.get("records"), list):
                    return js
            except Exception:
                pass
    return {"version": 1, "records": []}


def make_record(key, t, res, n_items, platform, rel_orbit, ref_mode, ref_dates):
    t_end = int(math.ceil(t.timestamp() / 3600.0) * 3600)
    return {"key": key, "src": "s1", "t_acq": int(t.timestamp()), "t_end": t_end, "win_h": WIN_H, "t_src": "acq",
            "n_feat": n_items, "platform": platform, "rel_orbit": rel_orbit, "ref_mode": ref_mode, "ref_dates": ref_dates,
            "fetched": int(time.time()), **res}


def save(site, arch, n_hex, new, skipped=()):
    have = {r["key"]: r for r in arch.get("records", [])}
    for r in new: have[r["key"]] = r
    recs = sorted(have.values(), key=lambda r: r["t_end"])[-MAX_RECORDS:]
    sk = [k for k in list(arch.get("skipped", [])) + list(skipped) if k not in have][-200:]
    out = {"version": 1, "n_hex": int(n_hex), "updated": int(time.time()), "records": recs, "skipped": sorted(set(sk))}
    pth = _paths(site)[0]; os.makedirs(os.path.dirname(pth), exist_ok=True)
    json.dump(out, open(pth, "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))
    return out


def write_geojson(site, rec):
    """hex ที่ท่วม ≥ 10% ของระเบียนล่าสุด → data/live/s1_flood.geojson (ใช้รูปทรงจาก data/static/hex.geojson)"""
    hp = os.path.join(site, "data", "static", "hex.geojson")
    out = os.path.join(site, "data", "live", "s1_flood.geojson")
    if not rec or not os.path.exists(hp): return
    hexes = json.load(open(hp, encoding="utf8"))["features"]
    by = {int(f["properties"].get("i", k)): f["geometry"] for k, f in enumerate(hexes)}
    when = datetime.fromtimestamp(rec["t_acq"], TZ).strftime("%Y-%m-%d %H:%M")
    feats = [{"type": "Feature", "geometry": by[i],
              "properties": {"hex": i, "ท่วม_%": round(fr * 100), "ถ่ายเมื่อ": when, "ดาวเทียม": rec.get("platform") or "Sentinel-1"}}
             for i, fr in zip(rec["hex"], rec["frac"]) if fr >= 0.10 and i in by]
    json.dump({"type": "FeatureCollection", "properties": {"key": rec["key"], "t_acq": rec["t_acq"], "km2": rec["km2"]},
               "features": feats}, open(out, "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))


def write_status(site, st):
    pth = os.path.join(site, "data", "live", "s1_status.json"); os.makedirs(os.path.dirname(pth), exist_ok=True)
    json.dump(st, open(pth, "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- main
def run_stac(site, days=LOOKBACK_D, max_passes=4, reprocess=False):
    p = json.load(open(os.path.join(site, "data", "static", "params.json"), encoding="utf8"))
    hk = p.get("hex_km2", 1.0); grid = Grid(p["lon"], p["lat"])
    arch = load(site)
    if arch.get("n_hex") not in (None, p["n"]): arch = {"version": 1, "records": []}
    done = {r["key"] for r in arch["records"]} | set(arch.get("skipped", []))
    cat = stac_client(); now = datetime.now(timezone.utc)
    passes = group_passes(search(cat, grid.bounds_ll, now - timedelta(days=days), now))
    todo = [g for g in passes if reprocess or g["key"] not in done][-max_passes:]
    st = {"ok": True, "checked": now.astimezone(TZ).isoformat(timespec="seconds"), "passes_found": len(passes),
          "processed": [], "skipped": []}
    new, no_ref = [], []
    for g in todo:
        t1 = time.time()
        try:
            vv = mosaic([it.assets["vv"].href for it in g["items"]], grid)
            ref, mode, rdates = reference(cat, grid, grid.bounds_ll, g)
            if ref is None:                                        # จำไว้ไม่ให้ดาวน์โหลดซ้ำทุกรอบ (--reprocess เพื่อลองใหม่)
                st["skipped"].append({"key": g["key"], "why": "ไม่มีภาพอ้างอิงใน orbit เดียวกัน"}); no_ref.append(g["key"]); continue
            res, _ = classify(vv, ref, grid, p, hk)
            rec = make_record(g["key"], g["t"], res, len(g["items"]), g["k"][0], g["k"][1], mode, rdates)
            new.append(rec)
            st["processed"].append({"key": g["key"], "km2": rec["km2"], "thr_db": rec["thr_db"], "cov_hex": rec["cov_hex"],
                                    "ref": mode, "s": round(time.time() - t1)})
        except Exception as e:                                     # ภาพเสีย/เครือข่าย → ข้ามการผ่านนี้ ไม่ให้ทั้งรอบล้ม
            st["skipped"].append({"key": g["key"], "why": str(e)[:200]})
    out = save(site, arch, p["n"], new, no_ref)
    latest = out["records"][-1] if out["records"] else None
    write_geojson(site, latest)
    st.update({"records": len(out["records"]), "latest": latest and {k: latest[k] for k in ("key", "t_acq", "km2", "thr_db")}})
    write_status(site, st)
    return st


def run_local(site, flood_tif, ref_tif, t_iso, key=None):
    p = json.load(open(os.path.join(site, "data", "static", "params.json"), encoding="utf8"))
    hk = p.get("hex_km2", 1.0); grid = Grid(p["lon"], p["lat"])
    t = datetime.fromisoformat(t_iso)
    t = t if t.tzinfo else t.replace(tzinfo=TZ)
    vv = read_to_grid(flood_tif, grid); ref = read_to_grid(ref_tif, grid)
    res, _ = classify(vv, ref, grid, p, hk)
    key = key or "s1local-" + t.astimezone(TZ).strftime("%Y-%m-%dT%H%M")
    rec = make_record(key, t, res, 1, "local", None, "local", [os.path.basename(ref_tif)])
    arch = load(site)
    if arch.get("n_hex") not in (None, p["n"]): arch = {"version": 1, "records": []}
    out = save(site, arch, p["n"], [rec])
    write_geojson(site, out["records"][-1])
    st = {"ok": True, "checked": datetime.now(TZ).isoformat(timespec="seconds"), "processed": [
        {"key": key, "km2": rec["km2"], "thr_db": rec["thr_db"], "cov_hex": rec["cov_hex"], "ref": "local"}], "records": len(out["records"]),
        "latest": {k: out["records"][-1][k] for k in ("key", "t_acq", "km2", "thr_db")}}
    write_status(site, st)
    return st


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Sentinel-1 flood extent → hex (ไม่ต้องใช้ GISTDA API key)")
    ap.add_argument("--site", default=".")
    ap.add_argument("--days", type=int, default=LOOKBACK_D, help="ค้นภาพย้อนหลังกี่วัน")
    ap.add_argument("--max-passes", type=int, default=4, help="ประมวลผลการผ่านใหม่สูงสุดกี่ครั้งต่อรอบ")
    ap.add_argument("--reprocess", action="store_true", help="ทำซ้ำการผ่านที่เคยประมวลผลแล้ว")
    ap.add_argument("--local-flood"); ap.add_argument("--local-ref"); ap.add_argument("--time"); ap.add_argument("--key")
    a = ap.parse_args()
    try:
        if a.local_flood:
            if not (a.local_ref and a.time): ap.error("--local-flood ต้องใช้คู่กับ --local-ref และ --time")
            st = run_local(a.site, a.local_flood, a.local_ref, a.time, a.key)
        else:
            st = run_stac(a.site, a.days, a.max_passes, a.reprocess)
    except Exception as e:
        st = {"ok": False, "checked": datetime.now(TZ).isoformat(timespec="seconds"), "error": str(e)[:300]}
        write_status(a.site, st); print(json.dumps(st, ensure_ascii=False, indent=1)); sys.exit(1)
    print(json.dumps(st, ensure_ascii=False, indent=1))
