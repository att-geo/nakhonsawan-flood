# -*- coding: utf-8 -*-
"""
เทียบผลแบบจำลองเหตุการณ์ทั้งจังหวัด (event_2d.py --hid prov_ev) กับน้ำท่วมจาก Sentinel-1 (s1_province.py) รายภาพ / รายตำบล

  python pipeline/event_compare_prov.py --site . --s1 ../s1_prov --run hecras/prov_ev/runs/2021.npz [--year 2021]

- ภาพเรดาร์ทั้งสองวงโคจร (62 ขาลง, 172 ขาขึ้น) ภายในช่วงจำลอง ; จำแนกน้ำแบบเดียวกับ s1_province.analyze (split-based, ref แล้งรายวงโคจร)
- เทียบบนกริด S1 ย่อ 2 เท่า (~72 ม.) ; แบบจำลอง = ความลึก ≥ 0.3 ม. ณ ชั่วโมงที่ถ่ายภาพ (เรดาร์มองไม่เห็นน้ำตื้นใต้ต้นข้าว)
- ระยะเวลาท่วมของทั้งสองฝั่งคิดจากภาพชุดเดียวกัน สูตรเดียวกัน (ราย pixel) -> ท่วม ≥ 30 วัน : POD / FAR / CSI รายจังหวัด อำเภอ ตำบล
- ไม่นับ: แหล่งน้ำถาวร, cell แม่น้ำของแบบจำลอง, นอกจังหวัด
ผล: <run>_cmp.json + <run>_cmp.png (ท่วม ≥ 30 วัน: น้ำเงิน = ตรงกัน, ส้ม = จริงแต่จำลองไม่ถึง, เหลือง = จำลองเกิน, เทา = แหล่งน้ำถาวร)
"""
import argparse, json, math, os, sys
from datetime import date
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s1_province as S  # noqa: E402

F2 = 2                                                             # ย่อกริด S1
MIN_PATCH_KM2 = 0.5          # ผืนน้ำของแบบจำลองที่เล็กกว่านี้ = แอ่ง/หลุมใน DEM 300 ม. (1–5 cell) ไม่นับ — เหมือน MIN_PATCH ของท่าตะโก


def _patch(m, ca, min_km2=MIN_PATCH_KM2):
    from scipy import ndimage
    lab, n = ndimage.label(m, structure=np.ones((3, 3), bool))
    if n == 0:
        return m
    size = np.bincount(lab.ravel(), weights=ca.ravel())
    keep = size >= min_km2; keep[0] = False
    return keep[lab]


def _red(a, how):
    h, w = (a.shape[0] // F2) * F2, (a.shape[1] // F2) * F2
    b = a[:h, :w].reshape(h // F2, F2, w // F2, F2)
    return b.mean((1, 3)) >= 0.5 if how == "maj" else b.max((1, 3)) if how == "max" else b[:, 0, :, 0]


def model_index(meta, shape):
    """pixel S1 (ย่อ) -> (row, col) ของกริดแบบจำลอง Web Mercator"""
    h, w = shape
    lat = S.BBOX[3] - (np.arange(h) + 0.5) * S.DY * F2; lon = S.BBOX[0] + (np.arange(w) + 0.5) * S.DX * F2
    X = np.radians(lon) * 6378137.0; Y = np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * 6378137.0
    ex = meta["extent_webm"]; cw = meta["cell_webm"]; ny, nx = meta["shape"]
    ci = ((X - ex[0]) // cw).astype(np.int32); ri = ((ex[3] - Y) // cw).astype(np.int32)
    R, C = np.meshgrid(ri, ci, indexing="ij")
    ok = (R >= 0) & (R < ny) & (C >= 0) & (C < nx)
    return np.clip(R, 0, ny - 1), np.clip(C, 0, nx - 1), ok


def durations(stack_w, stack_v, t):
    """ระยะเวลา (วัน) ราย pixel จากลำดับภาพ — สูตรเดียวกับ s1_province.analyze"""
    shp = stack_w[0].shape
    t_p = np.full(shp, np.nan, np.float32); t_pp = np.full(shp, np.nan, np.float32); w_p = np.zeros(shp, bool)
    dur = np.zeros(shp, np.float32)
    for w, v, tt in zip(stack_w, stack_v, t):
        m = v & ~np.isnan(t_p)
        left = np.where(np.isnan(t_pp), tt - t_p, t_p - t_pp)
        dur[m] += w_p[m] * ((tt - t_p[m]) + left[m]) / 2
        t_pp[v] = t_p[v]; t_p[v] = tt; w_p[v] = w[v]
    m = ~np.isnan(t_p)
    gap = np.where(np.isnan(t_pp), 12.0, t_p - t_pp)
    dur[m] += w_p[m] * gap[m]
    return dur


def main(site, s1_dir, run, year=None, log=print):
    from scipy import ndimage
    r = np.load(run); keys = [str(k) for k in r["snap_dates"]]; snaps = r["snaps"]
    year = year or int(keys[0][:4])
    hs = os.path.join(site, "data", "static", "hotspots")
    d = np.load(os.path.join(hs, "prov_ev.npz")); meta = json.loads(str(d["meta"])); riv = d["river"] >= 0
    tam = json.load(open(os.path.join(site, "data", "static", "province_tambon.geojson"), encoding="utf8"))["features"]
    zone_f = S.rasterize(tam); zone = _red(zone_f, "sub"); nz = len(tam)
    shp = zone.shape
    R, C, ok = model_index(meta, shp)
    inprov = (zone >= 0) & ok
    ca = _red(S.pixel_km2(), "sub") * F2 * F2
    fs = sorted(os.listdir(s1_dir))
    # ภาพอ้างอิงแล้ง + แหล่งน้ำถาวร (มืดทุกวงโคจร)
    refs = {}; perm = np.ones(shp, bool); seen = np.zeros(shp, bool)
    for o in S.ORBITS:
        dry = [f for f in fs if f.startswith(str(year)) and f.endswith(f"_o{o}_dry.npz")]
        if not dry:
            continue
        with np.errstate(all="ignore"):
            ref = np.nanmedian(np.stack([S._load(os.path.join(s1_dir, f)) for f in dry]), 0)
        refs[o] = ref
        pr = _red(ref < -15, "maj"); okr = _red(~np.isnan(ref), "maj")
        seen |= okr; perm &= ~okr | pr
    perm &= seen
    excl = perm | riv[R, C] | ~inprov
    W_s, W_m, V, T, rows = [], [], [], [], []
    for k, key in enumerate(keys):
        o = int(key.split("_o")[1]); fn = os.path.join(s1_dir, key + "_wet.npz")
        if o not in refs or not os.path.exists(fn):
            continue
        db = S._load(fn); v = ~np.isnan(db)
        thr, _ = S.split_threshold(db, v)
        with np.errstate(invalid="ignore"):
            wat = v & (db < thr) & ((db - np.nan_to_num(refs[o], nan=0) < -3) | np.isnan(refs[o]))
        wat = ndimage.uniform_filter(wat.astype(np.float32), 3) > 0.5
        ws = _red(wat, "maj"); vv = _red(v, "maj") & ~excl
        wm = _patch((snaps[k].astype(np.float32)[R, C] >= 0.3) & ~excl, ca) & vv
        ws &= vv
        tp = (ws & wm).sum(); fn_ = (ws & ~wm).sum(); fp = (~ws & wm).sum()
        rows.append({"key": key, "thr_db": round(thr, 1), "obs_km2": round(float(ca[ws].sum()), 1), "mod_km2": round(float(ca[wm].sum()), 1),
                     "csi": round(float(tp / max(tp + fn_ + fp, 1)), 3)})
        W_s.append(ws); W_m.append(wm); V.append(vv); T.append(float(date.fromisoformat(key[:10]).toordinal()))
        log(f"  {key} obs {rows[-1]['obs_km2']:.0f} mod {rows[-1]['mod_km2']:.0f} CSI {rows[-1]['csi']}")
    ds = durations(W_s, V, T); dm = durations(W_m, V, T)
    ev = np.stack(V).any(0) & ~excl
    o30 = (ds >= 30) & ev; m30 = _patch((dm >= 30) & ev, ca)
    def score(msk):
        tp = float(ca[o30 & m30 & msk].sum()); fn_ = float(ca[o30 & ~m30 & msk].sum()); fp = float(ca[~o30 & m30 & msk].sum())
        return {"obs_km2": round(tp + fn_, 1), "mod_km2": round(tp + fp, 1), "pod": round(tp / max(tp + fn_, 1e-9), 2),
                "far": round(fp / max(tp + fp, 1e-9), 2), "csi": round(tp / max(tp + fn_ + fp, 1e-9), 2)}
    res = {"year": year, "run": os.path.basename(run), "n_img": len(rows), "images": rows,
           "daily_csi_mean": round(float(np.mean([x["csi"] for x in rows if x["obs_km2"] >= 20] or [0])), 3),
           "ge30d": score(np.ones(shp, bool)), "tambon": [], "district": {}}
    dist = sorted({f["properties"]["district"] for f in tam})
    for dn in dist:
        ks = [k for k, f in enumerate(tam) if f["properties"]["district"] == dn]
        res["district"][dn] = score(np.isin(zone, ks))
    for k, f in enumerate(tam):
        sc = score(zone == k); sc.update({"name": f["properties"]["name"], "district": f["properties"]["district"]})
        res["tambon"].append(sc)
    out = run[:-4] + "_cmp"
    json.dump(res, open(out + ".json", "w", encoding="utf8"), ensure_ascii=False)
    rgba = np.zeros(shp + (4,), np.uint8)
    rgba[o30 & m30] = (37, 99, 235, 220); rgba[o30 & ~m30] = (234, 88, 12, 220); rgba[~o30 & m30] = (250, 204, 21, 200)
    rgba[perm & inprov] = (148, 163, 184, 120)
    from PIL import Image
    Image.fromarray(rgba, "RGBA").save(out + ".png", optimize=True)
    np.savez_compressed(out + ".npz", ds=ds.astype(np.float16), dm=dm.astype(np.float16), ev=ev)
    log(f"{year}: ≥30 วัน {res['ge30d']} ; CSI รายภาพเฉลี่ย {res['daily_csi_mean']}")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="."); ap.add_argument("--s1", required=True); ap.add_argument("--run", required=True)
    ap.add_argument("--year", type=int, default=None)
    a = ap.parse_args()
    main(a.site, a.s1, a.run, a.year)
