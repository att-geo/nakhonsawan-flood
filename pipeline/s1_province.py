# -*- coding: utf-8 -*-
"""
น้ำท่วมในอดีตจาก Sentinel-1 (2560–2568) ทั้งจังหวัดนครสวรรค์ — ขยายวิธีเดียวกับแอ่งท่าตะโก (`s1_history.py`) เป็นรายตำบลทั้งจังหวัด

ต่างจากท่าตะโก:
  - วงโคจร 62 (ขาลง) ครอบจังหวัดแค่ ~90% (ขาดฝั่งตะวันตก แม่วงก์/ชุมตาบง/แม่เปิน) -> ใช้ **62 + 172 (ขาขึ้น, ครอบ ~100%) รวมกัน**
    ภาพอ้างอิงฤดูแล้งแยกตามวงโคจร ; ระยะเวลาท่วมคิดจากลำดับเวลาราย pixel ของทั้งสองวงโคจร (ช่วงห่างภาพสั้นลง ~2 เท่าหลังปี 2565)
  - ภาพแต่ละการผ่าน (1–2 frame) ตัดที่ server ทั้งจังหวัด 5200×3800 (~36 ม. เท่าเดิม) แล้ว mosaic
  - เก็บภาพเป็น uint8 (dB, ขั้น 0.2 dB, −35…+16 dB) เพื่อประหยัดที่ (~10 MB/การผ่าน)

  python s1_province.py --out ../s1_prov --site . --download      # ~2–3 ชม. (4 threads)
  python s1_province.py --out ../s1_prov --site .                 # วิเคราะห์อย่างเดียว

จำแนกน้ำ (เหมือนท่าตะโก): VV_dB < หุบ histogram (−17…−13 dB) และ VV − ref_dry(วงโคจรเดียวกัน, ปีเดียวกัน) < −3 dB ; majority 3×3 ;
ตัดแหล่งน้ำถาวร (ref < −15 dB) ; ฤดู 1 ส.ค.–15 ธ.ค.
ระยะเวลาท่วม (วัน) ราย pixel = Σ w_i·(t_{i+1} − t_{i−1})/2 บนภาพที่ pixel นั้นมีข้อมูล (ขอบใช้ช่วงห่างที่ติดกัน) — สูตรเดียวกับเดิมแต่คิดราย pixel
"""
import io, json, os, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import numpy as np

BBOX = (99.08, 15.05, 100.84, 16.34); W, H = 5200, 3800
DX = (BBOX[2] - BBOX[0]) / W; DY = (BBOX[3] - BBOX[1]) / H
ORBITS = (62, 172)
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
DATA = "https://planetarycomputer.microsoft.com/api/data/v1/item/bbox"


def pixel_km2():
    lat = BBOX[3] - (np.arange(H) + 0.5) * DY
    return ((DX * 111.32 * np.cos(np.radians(lat)))[:, None] * (DY * 110.57) * np.ones((1, W))).astype(np.float32)


def _valley(v, lo=-17.0, hi=-13.0):
    h, e = np.histogram(v, bins=160, range=(-30, 10)); c = (e[:-1] + e[1:]) / 2
    hs = np.convolve(h, np.ones(5) / 5, mode="same")
    k = (c >= lo) & (c <= hi)
    return float(c[k][np.argmin(hs[k])])


def split_threshold(db, valid, ts=300, lo_frac=0.1, hi_frac=0.9):
    """เกณฑ์น้ำแบบ split-based (Martinis et al. 2009): แบ่ง tile ~0.1° คัดเฉพาะ tile ที่มีทั้งน้ำและบก (พิกเซล < −16 dB 10–90%)
    แล้วหาหุบ histogram จากพิกเซลรวมของ tile เหล่านั้น -> เกณฑ์เดียวต่อภาพ
    เหตุผล: หุบของทั้งจังหวัดถูกดึงลงไปที่ขอบ −17 dB เพราะพื้นบกมากกว่ามาก (2 ต.ค. 2564: ทั้งจังหวัด −16.6, กรอบท่าตะโกเดิม −15.1 dB) ;
    split-based ได้ใกล้กรอบท่าตะโกเดิม (−15.1/−15.1, −13.6/−14.1, −13.1/−13.1 dB) ; ไม่มี tile ผ่านเกณฑ์ (< 3) = ไม่มีน้ำท่วม -> ใช้หุบทั้งภาพ"""
    pool = []
    for i in range(0, H - ts + 1, ts):
        for j in range(0, W - ts + 1, ts):
            m = valid[i:i + ts, j:j + ts]
            if m.mean() < 0.8:
                continue
            x = db[i:i + ts, j:j + ts][m]; fw = (x < -16).mean()
            if lo_frac <= fw <= hi_frac:
                pool.append(x)
    if len(pool) < 3:
        return _valley(db[valid]), len(pool)
    return _valley(np.concatenate(pool)), len(pool)


def rasterize(features):
    """ตำบล -> index (-1 = นอกจังหวัด) ด้วย rasterio.features"""
    from rasterio import features as rf
    from rasterio.transform import from_origin
    tr = from_origin(BBOX[0], BBOX[3], DX, DY)
    return rf.rasterize(((f["geometry"], k) for k, f in enumerate(features)), out_shape=(H, W), transform=tr,
                        fill=-1, dtype="int16")


# ---------------------------------------------------------------------------------------------- download
def _post(q):
    r = urllib.request.Request(STAC, data=json.dumps(q).encode(), headers={"Content-Type": "application/json"})
    for k in range(5):
        try:
            return json.load(urllib.request.urlopen(r, timeout=120))["features"]
        except Exception:
            time.sleep(5 * (k + 1))
    raise RuntimeError("STAC search failed")


def passes(dt, orbit):
    """กลุ่ม frame ของการผ่านเดียวกัน -> {key: [item ids]} ; key = YYYY-MM-DD_o<orbit>"""
    fs = _post({"collections": ["sentinel-1-rtc"], "bbox": list(BBOX), "datetime": dt, "limit": 500,
                "query": {"sat:relative_orbit": {"eq": orbit}}})
    out = {}
    for f in fs:
        out.setdefault(f"{f['properties']['datetime'][:10]}_o{orbit}", []).append(f["id"])
    return out


def _fetch(iid):
    import rasterio
    u = (f"{DATA}/{BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]}/{W}x{H}.tif"
         f"?collection=sentinel-1-rtc&item={iid}&assets=vv&nodata=0&resampling=average")
    for k in range(5):
        try:
            b = urllib.request.urlopen(u, timeout=180).read()
            with rasterio.MemoryFile(b) as m, m.open() as ds:
                a = ds.read(1).astype(np.float32); msk = ds.read(ds.count) > 0 if ds.count > 1 else a > 0
            return np.where((a > 0) & msk, a, 0)
        except Exception as e:
            err = e; time.sleep(10 * (k + 1))
    raise RuntimeError(f"{iid}: {err}")


def _save_pass(out_dir, key, tag, ids):
    fn = os.path.join(out_dir, f"{key}_{tag}.npz")
    if os.path.exists(fn):
        return None
    lin = np.zeros((H, W), np.float32)
    for iid in ids:
        a = _fetch(iid); lin = np.where(lin > 0, lin, a)
    ok = lin > 0
    if ok.mean() < 0.02:
        return f"{key} empty"
    q = np.zeros((H, W), np.uint8)                                  # 0 = nodata ; dB = −35 + 0.2·(q−1)
    db = 10 * np.log10(np.where(ok, lin, 1))
    q[ok] = np.clip(np.round((db[ok] + 35) / 0.2) + 1, 1, 255).astype(np.uint8)
    np.savez_compressed(fn, q=q, ids=np.array(ids))
    return fn


def download(out_dir, years=range(2017, 2026), n_dry=6, threads=4, log=print):
    os.makedirs(out_dir, exist_ok=True)
    jobs = []
    for y in years:
        for o in ORBITS:
            for key, ids in passes(f"{y}-08-01/{y}-12-15", o).items():
                jobs.append((key, "wet", ids))
            dry = sorted(passes(f"{y}-02-01/{y}-04-30", o).items())
            if len(dry) > n_dry:                                    # กระจายให้ครอบ ก.พ.–เม.ย.
                dry = [dry[int(round(i))] for i in np.linspace(0, len(dry) - 1, n_dry)]
            jobs += [(k, "dry", ids) for k, ids in dry]
    log(f"{len(jobs)} การผ่าน ({sum(len(j[2]) for j in jobs)} frame)")
    t0 = time.time()
    with ThreadPoolExecutor(threads) as ex:
        for i, r in enumerate(ex.map(lambda j: _save_pass(out_dir, *j), jobs)):
            if r:
                log(f"[{i + 1}/{len(jobs)} {time.time() - t0:.0f}s] {os.path.basename(r)}")


# ---------------------------------------------------------------------------------------------- analyze
def _load(fn, edge=15):
    """dB ; ตัดขอบ footprint ของภาพ ~0.5 กม. (edge px) — ขอบ swath มี border noise มืดผิดปกติ ถูกนับเป็นน้ำ/แหล่งน้ำถาวร"""
    from scipy import ndimage
    q = np.load(fn)["q"]; v = q > 0
    if edge:
        v = ndimage.uniform_filter(v.astype(np.float32), 2 * edge + 1, mode="nearest") > 0.999
    return np.where(v, -35 + 0.2 * (q.astype(np.float32) - 1), np.nan).astype(np.float32)


def analyze(out_dir, site, season=("08-01", "12-15"), min_days=30, orbits=ORBITS, years_only=None, out_name="s1_province.json", log=print):
    from scipy import ndimage
    fs = sorted(f for f in os.listdir(out_dir) if f.endswith(".npz") and f[:4].isdigit())
    years = sorted({int(f[:4]) for f in fs if f.endswith("_wet.npz")})
    if years_only:
        years = [y for y in years if y in years_only]
    ca = pixel_km2()
    tam = json.load(open(os.path.join(site, "data", "static", "province_tambon.geojson"), encoding="utf8"))["features"]
    zone = rasterize(tam); nz = len(tam); inprov = zone >= 0
    zc = np.where(inprov, zone, nz)                                  # index สำหรับ bincount (nz = นอกจังหวัด)
    zkm2 = lambda m: np.bincount(zc[m], weights=ca[m], minlength=nz + 1)[:nz]
    res = {"bbox": BBOX, "size": [H, W], "orbits": list(orbits), "years": {},
           "tambon": [{k: f["properties"][k] for k in ("code", "name", "district", "area_km2")} for f in tam],
           "method": "VV<หุบ histogram(−17…−13 dB) & VV−ref_dry(วงโคจรเดียวกัน)<−3 dB, majority 3×3, 1 ส.ค.–15 ธ.ค., "
                     "relative orbit 62 (ขาลง) + 172 (ขาขึ้น) รวมเป็นลำดับเวลาราย pixel"}
    dur_all = np.zeros((len(years), H, W), np.float16); perm_any = np.zeros((H, W), bool)
    nobs_all = np.zeros((len(years), H, W), np.uint8)
    for yi, y in enumerate(years):
        refs = {}; perm = np.ones((H, W), bool); seen = np.zeros((H, W), bool)
        for o in orbits:
            dry = [f for f in fs if f.startswith(str(y)) and f.endswith(f"_o{o}_dry.npz")]
            if not dry:
                cand = sorted((f for f in fs if f.endswith(f"_o{o}_dry.npz")), key=lambda f: abs(int(f[:4]) - y))
                dry = [f for f in cand if f[:4] == cand[0][:4]] if cand else []
            if dry:
                st = np.stack([_load(os.path.join(out_dir, f)) for f in dry])
                with np.errstate(all="ignore"):
                    refs[o] = np.nanmedian(st, 0)
                del st
                ok = ~np.isnan(refs[o]); seen |= ok
                perm &= ~ok | (refs[o] < -15)                        # ถาวร = มืดในทุกวงโคจรที่มีข้อมูล (ขาขึ้นและขาลง)
        perm &= seen
        perm_any |= perm
        wet = sorted((f for f in fs if f.startswith(str(y)) and f.endswith("_wet.npz")
                      and f"{y}-{season[0]}" <= f[:10] <= f"{y}-{season[1]}"),
                     key=lambda f: (f[:10], f))
        wet = [f for f in wet if int(f.split("_o")[1].split("_")[0]) in refs]
        if not wet:
            continue
        # สะสมระยะเวลาแบบ streaming ราย pixel: dur += w_prev·(t − t_prevprev)/2 เมื่อเห็นภาพถัดไป
        t_p = np.full((H, W), np.nan, np.float32); w_p = np.zeros((H, W), bool)
        t_pp = np.full((H, W), np.nan, np.float32)
        dur = np.zeros((H, W), np.float32); nobs = np.zeros((H, W), np.uint8)
        area_ts = []; zone_ts = []
        for f in wet:
            o = int(f.split("_o")[1].split("_")[0]); t = float(date.fromisoformat(f[:10]).toordinal())
            db = _load(os.path.join(out_dir, f)); v = ~np.isnan(db)
            T, n_tile = split_threshold(db, v)
            wat = v & (db < T)
            ref = refs[o]
            with np.errstate(invalid="ignore"):
                wat &= (db - np.nan_to_num(ref, nan=0) < -3) | np.isnan(ref)
            wat = ndimage.uniform_filter(wat.astype(np.float32), 3) > 0.5
            wat &= v & ~perm
            # ภาพก่อนหน้าของ pixel ที่มีข้อมูลตอนนี้ได้ข้อมูลครบสองข้างแล้ว -> ปิดบัญชี
            m = v & ~np.isnan(t_p)
            left = np.where(np.isnan(t_pp), t - t_p, t_p - t_pp)       # ภาพแรก: ใช้ช่วงห่างด้านขวาแทน
            dur[m] += w_p[m] * ((t - t_p[m]) + left[m]) / 2
            t_pp[v] = t_p[v]; t_p[v] = t; w_p[v] = wat[v]; nobs[v] += 1
            a = zkm2(wat & inprov)
            area_ts.append([f[:10], o, round(float(a.sum()), 1), round(T, 1), round(float((v & inprov).mean() / inprov.mean()), 3)])
            zone_ts.append(a.round(2).tolist())
            log(f"  {f}  T={T:.1f} dB  ท่วม {a.sum():.0f} กม²")
        m = ~np.isnan(t_p)                                             # ภาพสุดท้ายของแต่ละ pixel
        gap = np.where(np.isnan(t_pp), 12.0, t_p - t_pp)
        dur[m] += w_p[m] * gap[m]                                      # (ช่วงซ้าย + ช่วงขวาเท่ากัน)/2
        dur_all[yi] = dur; nobs_all[yi] = nobs
        per = []
        for k in range(nz):
            mk = zone == k; d = dur[mk]
            per.append({"flood_km2_max": round(max(zt[k] for zt in zone_ts), 2),
                        "ge30d_km2": round(float(ca[mk][d >= 30].sum()), 2), "ge60d_km2": round(float(ca[mk][d >= 60].sum()), 2),
                        "ge90d_km2": round(float(ca[mk][d >= 90].sum()), 2),
                        "dur_p90_d": round(float(np.percentile(d[d > 0], 90)), 0) if (d > 0).any() else 0,
                        "n_obs_med": int(np.median(nobs[mk]))})
        res["years"][str(y)] = {"n_img": len(wet), "dates": [a[0] for a in area_ts], "orbit": [a[1] for a in area_ts],
                                "area_prov_km2": [a[2] for a in area_ts], "thr_db": [a[3] for a in area_ts],
                                "cov": [a[4] for a in area_ts], "tambon": per, "tambon_ts": zone_ts,
                                "prov_ge30d_km2": round(float(ca[inprov & (dur >= 30)].sum()), 1),
                                "prov_ge60d_km2": round(float(ca[inprov & (dur >= 60)].sum()), 1)}
        log(y, len(wet), res["years"][str(y)]["prov_ge30d_km2"], res["years"][str(y)]["prov_ge60d_km2"])
    freq30 = (dur_all >= min_days).sum(0).astype(np.uint8); freq60 = (dur_all >= 60).sum(0).astype(np.uint8)
    res["n_years"] = len(years); res["year_list"] = years
    res["perm_water_km2"] = round(float(ca[perm_any & inprov].sum()), 1)
    res["freq"] = []
    for k in range(nz):
        mk = zone == k
        res["freq"].append({"ge30d_in_ge3y_km2": round(float(ca[mk & (freq30 >= 3)].sum()), 2),
                            "ge30d_in_ge5y_km2": round(float(ca[mk & (freq30 >= 5)].sum()), 2),
                            "ge60d_in_ge3y_km2": round(float(ca[mk & (freq60 >= 3)].sum()), 2),
                            "mean_years_ge30d": round(float(freq30[mk].mean()), 2),
                            "perm_km2": round(float(ca[mk & perm_any].sum()), 2)})
    np.savez_compressed(os.path.join(out_dir, out_name.replace(".json", "_arrays.npz")), dur=dur_all, years=np.array(years), nobs=nobs_all,
                        freq30=freq30, freq60=freq60, zone=zone, perm=perm_any)
    json.dump(res, open(os.path.join(out_dir, out_name), "w", encoding="utf8"), ensure_ascii=False)
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--site", default=".")
    ap.add_argument("--download", action="store_true"); ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--years", default="2017-2025"); ap.add_argument("--no-analyze", action="store_true")
    a = ap.parse_args()
    y0, y1 = (int(x) for x in a.years.split("-"))
    if a.download:
        download(a.out, years=range(y0, y1 + 1), threads=a.threads)
    if not a.no_analyze:
        analyze(a.out, a.site)
