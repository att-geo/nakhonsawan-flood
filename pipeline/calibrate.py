# -*- coding: utf-8 -*-
"""
สอบเทียบพารามิเตอร์โมเดล hex network (v3) กับ (ก) ผลแบบจำลอง 2D ของจุดวิกฤต และ (ข) พื้นที่น้ำท่วมจากดาวเทียม
(GISTDA ถ้ามี API key และ/หรือ Sentinel-1 ที่แปลเองด้วย pipeline/s1_obs.py — ไม่ต้องใช้ key)

  python pipeline/run_update.py --site .        # สร้าง sim inputs (temp) + hex_state + เก็บ GISTDA รายวันลง data/live/gistda_obs.json
  python pipeline/run_hotspots.py --site .      # ผล 2D (พื้นที่ท่วมรายชั่วโมง + สัดส่วนท่วมราย hex)
  python pipeline/s1_obs.py --site .            # (ไม่บังคับ) แปลน้ำท่วมจาก Sentinel-1 → data/live/s1_obs.json
  python pipeline/calibrate.py --site .         # grid search -> data/static/calibration.json

ตัวแปรที่ปรับ: t_mult (เวลาระบาย), weir_c (สัมประสิทธิ์การล้นข้ามจุดล้น), dcap_mult (ความจุแอ่ง)

คะแนน (ต่ำ = ดี) = คะแนน 2D + w × พจน์ GISTDA
  คะแนน 2D   = RMSE สัมพัทธ์ของพื้นที่ท่วม ≥10 ซม. รายชั่วโมงในโดเมน 2D + 0.5 × ความต่างสัดส่วนท่วมราย hex (สูงสุด 72 ชม.)
  พจน์ GISTDA = (1 − CSI ราย hex) + 0.25 × ความคลาดเคลื่อนสัมพัทธ์ของพื้นที่รวม  เฉลี่ยตามระเบียนภาพดาวเทียม
    - เทียบ "สัดส่วนท่วมสูงสุดของโมเดลในหน้าต่างเวลา" กับภาพรายวัน (ดู gistda_obs.py) ไม่ใช่ ณ ชั่วโมงเดียว
    - hex ท่วมเมื่อสัดส่วนพื้นที่ท่วม ≥ 10% ; ไม่นับ hex แหล่งน้ำถาวร
    - ระเบียนที่พบน้ำท่วมรวม < 5 กม² ไม่ใช้ (แยก "ไม่ท่วม" ออกจาก "ไม่มีภาพ/เมฆบัง" ไม่ได้)
    - w = 1 × min(จำนวนระเบียน, 3)/3 → มีภาพน้อยกว่า 3 ระเบียนน้ำหนักลดลง กันสอบเทียบเข้าหาภาพเดียว
  ถ้ายังไม่มีระเบียน GISTDA ที่ใช้ได้ คะแนนเท่ากับแบบเดิม (2D อย่างเดียว)
"""
import argparse, itertools, json, os, sys, tempfile, time
from datetime import datetime, timezone, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402
import gistda_obs as gobs  # noqa: E402
import s1_obs  # noqa: E402  (อ่าน/เขียน JSON อย่างเดียว — ไม่ต้องมี rasterio ตอนสอบเทียบ)

TZ = timezone(timedelta(hours=7))
GRID = {"t_mult": [0.5, 1.0, 2.0], "weir_c": [0.3, 1.0, 3.0], "dcap_mult": [0.7, 1.0, 1.3]}
SIM_CACHE = os.path.join(tempfile.gettempdir(), "nsflood_sim_inputs.npz")
KEYS = ("t_mult", "weir_c", "dcap_mult")


def main(site, grid=None, use_gistda=True, use_s1=True):
    t0 = time.time()
    st = os.path.join(site, "data", "static"); lv = os.path.join(site, "data", "live")
    J = lambda d, n: json.load(open(os.path.join(d, n), encoding="utf8"))
    p = J(st, "params.json"); netj = J(st, "network.json")
    assets = J(st, "drainage_assets.json") if os.path.exists(os.path.join(st, "drainage_assets.json")) else None
    hs = {k: v for k, v in J(lv, "hotspots_live.json").items() if not k.startswith("_")}
    z = np.load(SIM_CACHE)
    P, ext, inj, bw = z["P"], z["ext"], z["inj"], z["bw"]; i_now = int(z["i_now"]); times = z["times"]
    prm = model.Params(p); hk = p.get("hex_km2", 1.0)
    recs = []                                                        # ระเบียนภาพดาวเทียม: GISTDA (รายวัน) + Sentinel-1 (ตามรอบโคจร, ไม่ต้องใช้ key)
    for use, loader in ((use_gistda, gobs.load), (use_s1, s1_obs.load)):
        if not use: continue
        a = loader(site)
        if a.get("n_hex") in (None, prm.n):                          # ผัง hex เปลี่ยน → ระเบียนเก่าใช้ไม่ได้
            recs += a.get("records", [])
    arch = {"records": recs}
    n_src = {k: sum(1 for r in recs if r.get("src", "gistda") == k) for k in ("gistda", "s1")}
    base = {}
    old = os.path.join(st, "calibration.json")
    if os.path.exists(old):
        oldj = json.load(open(old, encoding="utf8"))
        base = {k: v for k, v in oldj.items() if k in model.DEFAULT_CALIB}
    cur = dict(model.DEFAULT_CALIB, **base)
    if grid is None:                                                 # ค่าเริ่มต้น: ×0.5, ×1, ×2 รอบค่าปัจจุบัน (สอบเทียบซ้ำรายสัปดาห์ขยับได้)
        grid = {k: sorted({round(cur[k] * f, 3) for f in (0.5, 1.0, 2.0)}) for k in KEYS}

    cache = {}

    def run(vals):
        """คืน dict คะแนนของชุดพารามิเตอร์ (cache ตามค่า) — ชุดเดียวกันจะไม่จำลองซ้ำ"""
        kk = tuple(vals[k] for k in KEYS)
        if kk in cache: return cache[kk]
        cal = {**model.DEFAULT_CALIB, **base, **dict(zip(KEYS, kk))}
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
        s2d = float(np.mean(errs) + 0.5 * np.mean(ferr))
        g, rows = gobs.evaluate(arch, d, f, times, i_now, prm.is_water, hk)
        w = gobs.G_WEIGHT * min(g["n"], 3) / 3.0 if g else 0.0
        r = {"score": round(s2d + w * (g["term"] if g else 0.0), 4), "score_2d": round(s2d, 4),
             "rmse_rel": round(float(np.mean(errs)), 4), "hexfrac_mae": round(float(np.mean(ferr)), 4),
             "gistda": g, "gistda_rows": rows}
        cache[kk] = r
        return r

    results = []
    for vals in itertools.product(*grid.values()):
        r = run(dict(zip(grid.keys(), vals)))
        results.append({**dict(zip(grid.keys(), vals)), **{k: r[k] for k in ("score", "score_2d", "rmse_rel", "hexfrac_mae")},
                        **({"g_csi": r["gistda"]["csi"], "g_pod": r["gistda"]["pod"], "g_far": r["gistda"]["far"],
                            "g_area_err": r["gistda"]["area_rel_err"]} if r["gistda"] else {})})
        print(results[-1], flush=True)
    best = min(results, key=lambda r: r["score"])
    bk = {k: best[k] for k in grid}
    # คะแนนของค่าปัจจุบันและค่าตั้งต้น (1,1,1) ภายใต้นิยามคะแนนเดียวกัน — ไม่เทียบข้ามนิยาม (2D เดิม vs 2D+GISTDA)
    r_cur = run({k: cur[k] for k in KEYS}); r_dflt = run({k: model.DEFAULT_CALIB[k] for k in KEYS}); r_best = run({**{k: cur[k] for k in KEYS}, **bk})
    out = {**model.DEFAULT_CALIB, **base, **bk}
    edge = [k for k in grid if len(grid[k]) > 1 and bk[k] in (min(grid[k]), max(grid[k]))]
    g = r_best["gistda"]
    out.update({"score": best["score"], "previous_score": r_cur["score"], "baseline_score": r_dflt["score"],
                "scoring": ("2d+" + "+".join(sorted(g["by_src"]))) if g else "2d", "hotspots": list(hs.keys()),
                "at_grid_edge": edge,                                                 # พารามิเตอร์ที่ค่าดีสุดอยู่ขอบ grid → รอบหน้าจะขยับต่อ
                "calibrated_at": datetime.now(TZ).isoformat(timespec="seconds"),
                "method": ("grid search เทียบ (1) พื้นที่ท่วม ≥10 ซม. รายชั่วโมงและสัดส่วนท่วมราย hex กับแบบจำลอง 2D (local inertial 120 ม.)"
                           + (" และ (2) พื้นที่น้ำท่วมจากดาวเทียมราย hex — " + " + ".join(
                               {"gistda": "GISTDA (สูงสุดของโมเดลในหน้าต่าง 48–72 ชม.)",
                                "s1": f"Sentinel-1 SAR (สูงสุดของโมเดลใน {s1_obs.WIN_H} ชม. ก่อนเวลาถ่าย)"}[k] for k in sorted(g["by_src"]))
                              + " (CSI + ความคลาดเคลื่อนพื้นที่รวม)" if g else ""))})
    if g:
        out["gistda_eval"] = {**g, "weight": round(gobs.G_WEIGHT * min(g["n"], 3) / 3.0, 2), "score_2d_only": r_best["score_2d"],
                              "current_params": r_cur["gistda"], "default_params": r_dflt["gistda"], "records": r_best["gistda_rows"],
                              "records_available": n_src,
                              "note": "ภาพดาวเทียมไม่ใช่รายชั่วโมงและอาจไม่ครอบคลุมทั้งจังหวัด — ใช้เทียบค่าสูงสุดในหน้าต่างเวลา; ระเบียนที่ท่วมรวม < 5 กม² ไม่นับ; "
                                      "Sentinel-1 ไม่นับ hex ในเมือง/ที่สูง/นอกภาพ (nocov) เพราะ SAR มองน้ำท่วมตรงนั้นไม่เห็นหรือเห็นผิด"}
    else:
        out["gistda_eval"] = {"n": 0, "records_available": n_src,
                              "note": f"ยังไม่มีระเบียนภาพดาวเทียมที่ใช้ได้ (GISTDA {n_src['gistda']}, Sentinel-1 {n_src['s1']} ระเบียน; ต้องท่วมรวม ≥ {gobs.MIN_KM2:g} กม² และอยู่ในช่วง sim 30 วัน)"}
    out["grid"] = results
    json.dump(out, open(old, "w", encoding="utf8"), ensure_ascii=False, indent=1)
    print("best", bk, "score", best["score"], "baseline", out["baseline_score"], "scoring", out["scoring"],
          "edge", edge, "gistda", g and {k: g[k] for k in ("n", "csi", "pod", "far")}, round(time.time() - t0), "s")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--site", default="."); ap.add_argument("--no-gistda", action="store_true")
    ap.add_argument("--no-s1", action="store_true", help="ไม่ใช้ระเบียน Sentinel-1 (data/live/s1_obs.json)")
    a = ap.parse_args()
    main(a.site, use_gistda=not a.no_gistda, use_s1=not a.no_s1)
