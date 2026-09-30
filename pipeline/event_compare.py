# -*- coding: utf-8 -*-
"""
เทียบผลจำลองเหตุการณ์ (event_2d.py) กับพื้นที่ท่วมจาก Sentinel-1 วันเดียวกัน (s1_history.py)

  python pipeline/event_compare.py --site . --s1 ../s1_hist --run hecras/thatako_ev/runs/2021_b0.npz [--thr 0.3]

- น้ำจากภาพ: เกณฑ์เดียวกับ s1_history (หุบ histogram + ลดลง > 3 dB จากฤดูแล้ง, majority 3×3, ตัดน้ำถาวร)
- น้ำจากแบบจำลอง: ความลึก ≥ thr ณ ชั่วโมงที่ถ่ายภาพ (ภาพเรดาร์มองไม่เห็นน้ำตื้นใต้ต้นข้าว จึงใช้ thr 0.3 ม. เป็นค่าหลัก)
- ระยะเวลาท่วมของทั้งสองฝั่งคำนวณแบบเดียวกัน: Σ ท่วม × (t_ถัดไป − t_ก่อนหน้า)/2 ตามวันถ่ายภาพ
- เทียบเฉพาะใน AOI (ตำบลที่ศึกษา) + แยกรายตำบล ; CSI/POD/FAR ของพื้นที่ท่วมรายวัน และของพื้นที่ท่วม ≥ 30 วัน
"""
import argparse, json, math, os, sys
from datetime import date
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s1_history as S  # noqa: E402


def s1_masks(s1_dir, year, dates):
    from scipy import ndimage
    dry = sorted(f for f in os.listdir(s1_dir) if f.startswith(str(year)) and f.endswith("_dry.npz"))
    ref = np.nanmedian(np.stack([S._load(os.path.join(s1_dir, f)) for f in dry]), 0)
    perm = ref < -15
    out = {}
    for d in dates:
        db = S._load(os.path.join(s1_dir, f"{d}_wet.npz")); v = ~np.isnan(db)
        wat = v & (db < S._valley(db[v])) & ((db - np.nan_to_num(ref, nan=0) < -3) | np.isnan(ref))
        out[d] = (ndimage.uniform_filter(wat.astype(np.float32), 3) > 0.5) & ~perm
    return out, perm


def weights(dates):
    t = np.array([date.fromisoformat(d).toordinal() for d in dates], float)
    if len(t) == 1:
        return np.array([12.0])
    tt = np.concatenate([[t[0] - (t[1] - t[0])], t, [t[-1] + (t[-1] - t[-2])]])
    return (tt[2:] - tt[:-2]) / 2


def lookup(site, hid="thatako_ev"):
    d = np.load(os.path.join(site, "data", "static", "hotspots", f"{hid}.npz")); m = json.loads(str(d["meta"]))
    lon, lat = S.pixel_lonlat(); ex = m["extent_webm"]; cw = m["cell_webm"]; ny, nx = m["shape"]
    X = lon / 180 * math.pi * 6378137.0; Y = np.log(np.tan(math.pi / 4 + np.radians(lat) / 2)) * 6378137.0
    return np.clip(((ex[3] - Y) / cw).astype(int), 0, ny - 1), np.clip(((X - ex[0]) / cw).astype(int), 0, nx - 1)


def scores(mod, obs, ca):
    tp = ca[mod & obs].sum(); fp = ca[mod & ~obs].sum(); fn = ca[~mod & obs].sum()
    return {"obs_km2": round(float(tp + fn), 1), "mod_km2": round(float(tp + fp), 1),
            "POD": round(float(tp / max(tp + fn, 1e-9)), 2), "FAR": round(float(fp / max(tp + fp, 1e-9)), 2),
            "CSI": round(float(tp / max(tp + fp + fn, 1e-9)), 2)}


def compare(site, s1_dir, run_npz, thr=0.3, hid="thatako_ev"):
    r = np.load(run_npz); dates = [str(x) for x in r["snap_dates"]]
    year = int(dates[0][:4])
    obs, perm = s1_masks(s1_dir, year, dates)
    ri, ci = lookup(site, hid)
    zone = S._rasterize(json.load(open(os.path.join(site, "data", "static", "hotspots", "thatako_aoi.geojson"), encoding="utf8"))["features"])
    names = [f["properties"]["name"] for f in json.load(open(os.path.join(site, "data", "static", "hotspots", "thatako_aoi.geojson"), encoding="utf8"))["features"]]
    ca = S.pixel_km2(); aoi = (zone >= 0) & ~perm
    w = weights(dates)
    daily = []; dur_o = np.zeros(zone.shape, np.float32); dur_m = np.zeros(zone.shape, np.float32)
    ts = {"obs": [], "mod": []}
    for k, d in enumerate(dates):
        mo = (r["snaps"][k].astype(np.float32)[ri, ci] >= thr) & ~perm
        ob = obs[d]
        dur_o += ob * w[k]; dur_m += mo * w[k]
        daily.append({"date": d, **scores(mo & aoi, ob & aoi, ca)})
        ts["obs"].append(np.bincount(zone[ob & aoi], weights=ca[ob & aoi], minlength=len(names)).round(1).tolist())
        ts["mod"].append(np.bincount(zone[mo & aoi], weights=ca[mo & aoi], minlength=len(names)).round(1).tolist())
    long_ = scores((dur_m >= 30) & aoi, (dur_o >= 30) & aoi, ca)
    tamb = []
    for i, n in enumerate(names):
        z = (zone == i) & ~perm
        tamb.append({"name": n, "obs_ge30d_km2": round(float(ca[z & (dur_o >= 30)].sum()), 1), "mod_ge30d_km2": round(float(ca[z & (dur_m >= 30)].sum()), 1),
                     "obs_peak_km2": max(t[i] for t in ts["obs"]), "mod_peak_km2": max(t[i] for t in ts["mod"])})
    csi_mean = round(float(np.mean([x["CSI"] for x in daily if x["obs_km2"] > 5])), 3)
    bias = round(float(np.mean([(x["mod_km2"] - x["obs_km2"]) / max(x["obs_km2"], 5) for x in daily])), 3)
    return {"run": os.path.basename(run_npz), "thr_m": thr, "n_dates": len(dates), "daily": daily, "ge30d": long_,
            "csi_daily_mean": csi_mean, "area_bias_mean": bias, "tambon": tamb, "ts": ts, "dates": dates}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="."); ap.add_argument("--s1", required=True); ap.add_argument("--run", required=True)
    ap.add_argument("--thr", type=float, default=0.3)
    a = ap.parse_args()
    res = compare(a.site, a.s1, a.run, a.thr)
    json.dump(res, open(a.run.replace(".npz", f"_cmp{int(a.thr * 100)}.json"), "w", encoding="utf8"), ensure_ascii=False)
    print(res["run"], "CSI(daily mean)", res["csi_daily_mean"], "bias", res["area_bias_mean"], "ge30d", res["ge30d"])
