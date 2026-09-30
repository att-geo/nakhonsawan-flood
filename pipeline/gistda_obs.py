# -*- coding: utf-8 -*-
"""
เก็บพื้นที่น้ำท่วมจากดาวเทียม GISTDA เป็น "สัดส่วนท่วมราย hex" แล้วใช้เป็นเป้าหมายสอบเทียบโมเดล

ทำไมต้องมีไฟล์นี้
  - GISTDA flood/3days เป็นหน้าต่างเลื่อน 3 วัน ถ้าไม่เก็บไว้ ภาพเก่าจะหายไป สอบเทียบย้อนหลังไม่ได้
  - polygon ดิบมีขนาดใหญ่ → แปลงเป็นสัดส่วนพื้นที่ท่วมราย hex 1 กม² (ตรงกับ `f` ของโมเดล v3) แล้วเก็บเฉพาะ hex ที่ท่วม
  - ภาพดาวเทียมเป็นรายวัน ไม่ใช่รายชั่วโมง และอาจไม่ครอบคลุมทุกพื้นที่ (เมฆ/แนวโคจรไม่ผ่าน/ไม่มีภาพ)
    → เทียบกับ "ค่าสูงสุดของโมเดลในหน้าต่างเวลา" ไม่ใช่ ณ เวลาเดียว และไม่ใช้วันที่ไม่พบน้ำท่วมเลยเป็นหลักฐานว่า "ไม่ท่วม"

ไฟล์ผลลัพธ์: data/live/gistda_obs.json  (และสำเนา data/static/gistda_obs.json ที่ commit รายสัปดาห์ไว้กันข้อมูลหาย)

ใช้แค่ stdlib + numpy (scipy ใช้เร่งความเร็วถ้ามี)
  python pipeline/gistda_obs.py --probe <gistda_flood.geojson>   # ตรวจ schema จริงของ API: field วันที่, ชนิด geometry, พื้นที่รวม
"""
import argparse, json, math, os, re, sys
from datetime import datetime, timezone, timedelta
import numpy as np

TZ = timezone(timedelta(hours=7))
CELL_M = 200.0            # ความละเอียดกริดสุ่มตัวอย่าง
MAX_RECORDS = 240         # เก็บย้อนหลังสูงสุดกี่ระเบียน (รายวัน ≈ 8 เดือน)
WIN_FEATURE_H = 48        # ฟีเจอร์มีวันที่ของตัวเอง: หน้าต่างเทียบโมเดล = 48 ชม. ก่อนสิ้นวันนั้น (เวลาที่ดาวเทียมผ่านไม่แน่นอน + น้ำท่วมคงอยู่)
WIN_FALLBACK_H = 72       # ไม่มีวันที่ในฟีเจอร์: ถือว่าเป็นภาพรวม 3 วันล่าสุด
MIN_KM2 = 5.0             # ระเบียนที่พบน้ำท่วมรวมน้อยกว่านี้ไม่ใช้สอบเทียบ (แยก "ไม่ท่วม" ออกจาก "ไม่มีภาพ" ไม่ได้)
FRAC_T = 0.10             # hex นับว่าท่วมเมื่อสัดส่วนพื้นที่ท่วม ≥ 10%
WARMUP_H = 72             # โมเดลเริ่มจำลองแห้ง (cold start) ไม่ใช้หน้าต่างที่เริ่มก่อน sim เริ่ม + 72 ชม.
G_WEIGHT = 1.0            # น้ำหนักของเป้าหมาย GISTDA เทียบกับเป้าหมาย 2D ในคะแนนรวม

_DATE_KEYS = re.compile(r"(date|time|dt$|acq|obs|flood_?d)", re.I)


# ---------------------------------------------------------------- เวลา
def _to_dt(v, ref):
    """แปลงค่าใน properties เป็น datetime (เวลาไทย) — รองรับ ISO, dd/mm/yyyy, epoch (วินาที/มิลลิวินาที), ปี พ.ศ."""
    if v is None or isinstance(v, bool):
        return None
    d = None
    try:
        if isinstance(v, (int, float)):
            x = float(v)
            if x > 1e11: x /= 1000.0
            if 8e8 < x < 4e9: d = datetime.fromtimestamp(x, TZ)
        else:
            s = str(v).strip()
            m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
            if m:
                d = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=TZ)
            else:
                d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1))
                d = d.astimezone(TZ) if d.tzinfo else d.replace(tzinfo=TZ)
    except Exception:
        return None
    if d is None:
        return None
    if d.year > 2400:                                            # ปี พ.ศ.
        d = d.replace(year=d.year - 543)
    return d if ref - timedelta(days=400) <= d <= ref + timedelta(days=2) else None


def feature_date(props, ref):
    """วันที่ของฟีเจอร์ (date เวลาไทย) หรือ None ถ้าหาไม่เจอ"""
    for k, v in (props or {}).items():
        if _DATE_KEYS.search(str(k)):
            d = _to_dt(v, ref)
            if d:
                return d.date()
    return None


# ---------------------------------------------------------------- เรขาคณิต
def _rings(geom):
    if not geom: return
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "Polygon": polys = [c]
    elif t == "MultiPolygon": polys = c
    else: return
    for poly in polys:
        for ring in poly:
            yield np.asarray(ring, float)[:, :2]


def _fill(mask, x0, y0, dx, dy, geoms):
    """scanline even-odd (รองรับรู) ของ polygon ทั้งหมดลงบน mask[row=lat, col=lon]"""
    ny, nx = mask.shape
    for g in geoms:
        rings = list(_rings(g))
        if not rings: continue
        ys = (np.arange(ny) + 0.5) * dy + y0
        par = np.zeros((ny, nx), np.uint8)
        for r in rings:
            if len(r) < 3: continue
            xa, ya, xb, yb = r[:-1, 0], r[:-1, 1], r[1:, 0], r[1:, 1]
            keep = ya != yb
            xa, ya, xb, yb = xa[keep], ya[keep], xb[keep], yb[keep]
            lo, hi = np.minimum(ya, yb), np.maximum(ya, yb)
            j0 = max(int(np.floor((lo.min() - y0) / dy - 0.5)), 0); j1 = min(int(np.ceil((hi.max() - y0) / dy)), ny - 1)
            for j in range(j0, j1 + 1):
                y = ys[j]
                on = (lo <= y) & (y < hi)
                if not on.any(): continue
                xc = np.sort(xa[on] + (y - ya[on]) * (xb[on] - xa[on]) / (yb[on] - ya[on]))
                if xc.size % 2: continue
                # ทุกคู่ (xc[2k], xc[2k+1]) คือช่วงที่อยู่ข้างใน -> สลับ parity ด้วย diff array
                i = np.clip(np.ceil((xc - x0) / dx - 0.5).astype(int), 0, nx)
                d = np.zeros(nx + 1, np.int32)
                np.add.at(d, i, 1)
                par[j] ^= (np.cumsum(d[:nx]) & 1).astype(np.uint8)
        mask |= par.astype(bool)
    return mask


def hex_fractions(feats, lon, lat, hex_km2=1.0):
    """polygon น้ำท่วม -> {index hex: สัดส่วนพื้นที่ท่วม 0-1} (สุ่มกริด CELL_M ม. แล้วให้ทุกเซลล์ไปที่ hex ที่ใกล้ที่สุด)"""
    lon = np.asarray(lon, float); lat = np.asarray(lat, float)
    geoms = [f.get("geometry") for f in feats if f.get("geometry")]
    if not geoms: return {}
    lat0 = float(lat.mean()); cosl = math.cos(math.radians(lat0))
    dy = CELL_M / 111320.0; dx = CELL_M / (111320.0 * cosl)
    x0, y0 = lon.min() - 0.03, lat.min() - 0.03
    nx = int((lon.max() + 0.03 - x0) / dx) + 1; ny = int((lat.max() + 0.03 - y0) / dy) + 1
    mask = _fill(np.zeros((ny, nx), bool), x0, y0, dx, dy, geoms)
    jj, ii = np.nonzero(mask)
    if jj.size == 0: return {}
    px = (x0 + (ii + 0.5) * dx) * cosl; py = y0 + (jj + 0.5) * dy
    hx, hy = lon * cosl, lat
    try:
        from scipy.spatial import cKDTree
        dist, idx = cKDTree(np.c_[hx, hy]).query(np.c_[px, py])
    except Exception:
        idx = np.empty(px.size, int); dist = np.empty(px.size)
        for a in range(0, px.size, 2000):
            d2 = (px[a:a + 2000, None] - hx[None, :]) ** 2 + (py[a:a + 2000, None] - hy[None, :]) ** 2
            idx[a:a + 2000] = d2.argmin(1); dist[a:a + 2000] = np.sqrt(d2.min(1))
    ok = dist * 111.32 <= 0.75                                   # เกินรัศมี hex 1 กม² (~0.62 กม.) = นอกพื้นที่ tessellation
    cell_km2 = (dy * 111.32) * (dx * 111.32 * cosl)
    cnt = np.bincount(idx[ok], minlength=lon.size)
    fr = np.minimum(cnt * cell_km2 / hex_km2, 1.0)
    nz = np.nonzero(fr >= 0.02)[0]
    return {int(i): float(fr[i]) for i in nz}


# ---------------------------------------------------------------- ที่เก็บ
def _paths(site):
    return os.path.join(site, "data", "live", "gistda_obs.json"), os.path.join(site, "data", "static", "gistda_obs.json")


def load(site):
    for p in _paths(site):
        if os.path.exists(p):
            try:
                js = json.load(open(p, encoding="utf8"))
                if isinstance(js, dict) and isinstance(js.get("records"), list):
                    return js
            except Exception:
                pass
    return {"version": 1, "records": []}


def update(site, feats, lon, lat, hex_km2=1.0, now=None):
    """เรียกทุกครั้งที่ดึง GISTDA สำเร็จ: แปลงเป็นระเบียนรายวัน รวมกับที่เก็บไว้ แล้วเขียน data/live/gistda_obs.json"""
    now = now or datetime.now(TZ)
    arch = load(site)
    if arch.get("n_hex") not in (None, len(lon)):                 # ผัง hex เปลี่ยน (สร้างชั้นข้อมูลคงที่ใหม่) → index เดิมใช้ไม่ได้
        arch = {"version": 1, "records": []}
    by = {}
    for f in feats:
        d = feature_date(f.get("properties"), now)
        by.setdefault(d, []).append(f)
    new = []
    for d, fs in by.items():
        fr = hex_fractions(fs, lon, lat, hex_km2)
        ids = sorted(fr)
        if d is None:                                             # ไม่มีวันที่: ภาพรวม 3 วันล่าสุด ผูกกับเวลาที่ดึง
            key = "fetch-" + now.strftime("%Y-%m-%d"); t_end = int(now.timestamp()) // 3600 * 3600
            win, src = WIN_FALLBACK_H, "fetch"
        else:
            key = d.isoformat()
            t_end = int(min(datetime(d.year, d.month, d.day, tzinfo=TZ) + timedelta(days=1), now).timestamp()) // 3600 * 3600
            win, src = WIN_FEATURE_H, "feature"
        new.append({"key": key, "t_end": t_end, "win_h": win, "t_src": src, "n_feat": len(fs),
                    "km2": round(sum(fr.values()) * hex_km2, 2), "fetched": int(now.timestamp()),
                    "hex": ids, "frac": [round(fr[i], 2) for i in ids]})
    have = {r["key"]: r for r in arch["records"]}
    for r in new:
        old = have.get(r["key"])
        if old is None or r["t_src"] == "fetch" or r["n_feat"] >= old.get("n_feat", 0):
            have[r["key"]] = r
    recs = sorted(have.values(), key=lambda r: r["t_end"])[-MAX_RECORDS:]
    out = {"version": 1, "n_hex": int(len(lon)), "updated": int(now.timestamp()), "records": recs}
    p = _paths(site)[0]
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(out, open(p, "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))
    return {"records": len(recs), "new_dates": [r["key"] for r in new], "new_km2": [r["km2"] for r in new]}


# ---------------------------------------------------------------- เทียบกับโมเดล
def evaluate(arch, d, f, times, i_now, is_water, hex_km2=1.0):
    """เทียบพื้นที่ท่วมสูงสุดของโมเดลในหน้าต่างเวลา กับภาพดาวเทียมราย hex (ระเบียน GISTDA และ Sentinel-1 ใช้รูปแบบเดียวกัน)
    d, f : (T, N) ความลึก (ซม.) และสัดส่วนพื้นที่ท่วมจาก simulate_v3 ; times : unix ชั่วโมง ; คืน (สรุป, รายระเบียน)"""
    N = d.shape[1]; land = ~np.asarray(is_water)
    t0, t1 = int(times[0]), int(times[min(i_now, len(times) - 1)])
    rows = []
    for r in arch.get("records", []):
        if r["km2"] < MIN_KM2: continue
        a_t = r["t_end"] - r["win_h"] * 3600
        if a_t < t0 + WARMUP_H * 3600 or r["t_end"] > t1: continue
        a = int(np.searchsorted(times, a_t)); b = int(np.searchsorted(times, r["t_end"], side="right"))
        if b - a < 2: continue
        mf = (f[a:b] * (d[a:b] >= 10)).max(0)
        of = np.zeros(N); of[np.asarray(r["hex"], int)] = r["frac"]
        ok = land.copy()
        if r.get("nocov"): ok[np.asarray(r["nocov"], int)] = False     # hex ที่ภาพไม่ครอบคลุม/เชื่อถือไม่ได้ (Sentinel-1) ไม่นับทั้งสองฝั่ง
        m_flag, o_flag = (mf >= FRAC_T) & ok, (of >= FRAC_T) & ok
        h = int((m_flag & o_flag).sum()); miss = int((~m_flag & o_flag).sum()); fa = int((m_flag & ~o_flag).sum())
        a_m, a_o = float(mf[ok].sum() * hex_km2), float(of[ok].sum() * hex_km2)
        rows.append({"key": r["key"], "src": r.get("src", "gistda"), "n_hex_obs": int(o_flag.sum()), "n_hex_model": int(m_flag.sum()),
                     "csi": round(h / max(h + miss + fa, 1), 3), "pod": round(h / max(h + miss, 1), 3),
                     "far": round(fa / max(h + fa, 1), 3), "km2_obs": round(a_o, 1), "km2_model": round(a_m, 1),
                     "bias": round(a_m / max(a_o, 1e-6), 2)})
    if not rows:
        return None, rows
    by_src = {}
    for x in rows: by_src.setdefault(x["src"], []).append(x["csi"])
    csi = float(np.mean([x["csi"] for x in rows]))
    brel = float(np.mean([min(abs(x["km2_model"] - x["km2_obs"]) / max(x["km2_obs"], 1e-6), 2.0) for x in rows]))
    return {"n": len(rows), "csi": round(csi, 4), "pod": round(float(np.mean([x["pod"] for x in rows])), 4),
            "far": round(float(np.mean([x["far"] for x in rows])), 4), "area_rel_err": round(brel, 4),
            "term": round((1 - csi) + 0.25 * brel, 4),
            "by_src": {k: {"n": len(v), "csi": round(float(np.mean(v)), 4)} for k, v in by_src.items()}}, rows


# ---------------------------------------------------------------- probe
def probe(path):
    js = json.load(open(path, encoding="utf8")); feats = js.get("features", [])
    now = datetime.now(TZ)
    print("features:", len(feats))
    if not feats: return
    keys = {}
    for f in feats:
        for k, v in (f.get("properties") or {}).items():
            keys.setdefault(k, set()).add(str(v)[:40])
    for k, vs in keys.items():
        print(f"  {k:24s} ตัวอย่าง {sorted(vs)[:3]}  ({len(vs)} ค่า)  {'<- ดูเหมือนวันที่' if _DATE_KEYS.search(k) else ''}")
    ds = {}
    for f in feats:
        ds.setdefault(feature_date(f.get("properties"), now), 0); ds[feature_date(f.get("properties"), now)] += 1
    print("วันที่ที่ตรวจพบ:", {str(k): v for k, v in sorted(ds.items(), key=lambda x: str(x[0]))})
    print("ชนิด geometry:", sorted({(f.get("geometry") or {}).get("type") for f in feats}, key=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--probe", required=True)
    probe(ap.parse_args().probe)
