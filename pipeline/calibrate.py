# -*- coding: utf-8 -*-
"""
สอบเทียบพารามิเตอร์โมเดล hex network (v3) กับผลแบบจำลอง 2D ของจุดวิกฤต

  python pipeline/run_update.py --site .        # สร้าง sim inputs (temp) + hex_state
  python pipeline/run_hotspots.py --site .      # ผล 2D (พื้นที่ท่วมรายชั่วโมง + สัดส่วนท่วมราย hex)
  python pipeline/calibrate.py --site .         # grid search -> data/static/calibration.json

ตัวแปรที่ปรับ: t_mult (เวลาระบาย), weir_c (สัมประสิทธิ์การล้นข้ามจุดล้น), dcap_mult (ความจุแอ่ง)
คะแนน = RMSE สัมพัทธ์ของพื้นที่ท่วม ≥10 ซม. รายชั่วโมงในโดเมน 2D + 0.5 × ความต่างสัดส่วนท่วมราย hex (สูงสุด 72 ชม.)
"""
import argparse, itertools, json, os, sys, tempfile, time
from datetime import datetime, timezone, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402

TZ = timezone(timedelta(hours=7))
GRID = {"t_mult": [0.5, 1.0, 2.0], "weir_c": [0.3, 1.0, 3.0], "dcap_mult": [0.7, 1.0, 1.3]}
SIM_CACHE = os.path.join(tempfile.gettempdir(), "nsflood_sim_inputs.npz")


def main(site, grid=None):
    t0 = time.time()
    st = os.path.join(site, "data", "static"); lv = os.path.join(site, "data", "live")
    J = lambda d, n: json.load(open(os.path.join(d, n), encoding="utf8"))
    p = J(st, "params.json"); netj = J(st, "network.json")
    assets = J(st, "drainage_assets.json") if os.path.exists(os.path.join(st, "drainage_assets.json")) else None
    hs = {k: v for k, v in J(lv, "hotspots_live.json").items() if not k.startswith("_")}
    z = np.load(SIM_CACHE)
    P, ext, inj, bw = z["P"], z["ext"], z["inj"], z["bw"]; i_now = int(z["i_now"]); times = z["times"]
    prm = model.Params(p); hk = p.get("hex_km2", 1.0)
    base = {}; base_score0 = None
    old = os.path.join(st, "calibration.json")
    if os.path.exists(old):
        oldj = json.load(open(old, encoding="utf8"))
        base = {k: v for k, v in oldj.items() if k in model.DEFAULT_CALIB}
        base_score0 = oldj.get("baseline_score")
    if grid is None:                                                 # ค่าเริ่มต้น: ×0.5, ×1, ×2 รอบค่าปัจจุบัน (สอบเทียบซ้ำรายสัปดาห์ขยับได้)
        cur = dict(model.DEFAULT_CALIB, **base)
        grid = {k: sorted({round(cur[k] * f, 3) for f in (0.5, 1.0, 2.0)}) for k in ("t_mult", "weir_c", "dcap_mult")}
    results = []
    for vals in itertools.product(*grid.values()):
        cal = {**model.DEFAULT_CALIB, **base, **dict(zip(grid.keys(), vals))}
        nw = model.Network(netj, p, assets, cal)
        t_end = min(P.shape[0], i_now + 73)
        d, f, _ = model.simulate_v3(P, prm, nw, bw, ext, inj, t_end=t_end)
        errs, ferr = [], []
        for k, h in hs.items():
            hx = np.array(h["hex"]); cov = np.array(h.get("hexcov", [1.0] * len(hx)))
            s2 = np.array(h["series"])                                     # [hour_unix, km2, vol]
            idx = np.searchsorted(times, s2[:, 0].astype(np.int64))
            ok = idx < t_end
            a_hex = np.array([(cov * f[i, hx] * (d[i, hx] >= model.FLOOD_CM)).sum() * hk for i in idx[ok]])
            a2d = s2[ok, 1]
            errs.append(np.sqrt(np.mean((a_hex - a2d) ** 2)) / max(a2d.mean(), 1.0))
            fm = (f[i_now:t_end, hx] * (d[i_now:t_end, hx] >= model.FLOOD_CM)).max(0)
            ferr.append(np.mean(np.abs(fm - np.array(h["hexfrac_max72"]))))
        score = float(np.mean(errs) + 0.5 * np.mean(ferr))
        results.append({**{k: cal[k] for k in grid}, "score": round(score, 4),
                        "rmse_rel": round(float(np.mean(errs)), 4), "hexfrac_mae": round(float(np.mean(ferr)), 4)})
        print(results[-1], flush=True)
    best = min(results, key=lambda r: r["score"])
    out = {**model.DEFAULT_CALIB, **base, **{k: best[k] for k in grid}}
    cur_score = next((r["score"] for r in results if all(r[k] == dict(model.DEFAULT_CALIB, **base)[k] for k in grid)), None)
    dflt = next((r["score"] for r in results if all(r[k] == 1.0 for k in grid)), None)
    out.update({"score": best["score"], "previous_score": cur_score,
                "baseline_score": base_score0 if base_score0 is not None else dflt, "hotspots": list(hs.keys()),
                "calibrated_at": datetime.now(TZ).isoformat(timespec="seconds"),
                "method": "grid search เทียบพื้นที่ท่วม ≥10 ซม. รายชั่วโมงและสัดส่วนท่วมราย hex กับแบบจำลอง 2D (local inertial 120 ม.)",
                "grid": results})
    json.dump(out, open(old, "w", encoding="utf8"), ensure_ascii=False, indent=1)
    print("best", {k: out[k] for k in grid}, "score", best["score"], "baseline", out["baseline_score"], round(time.time() - t0), "s")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--site", default=".")
    main(ap.parse_args().site)
