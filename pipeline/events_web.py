# -*- coding: utf-8 -*-
"""
สรุปผลจำลองเหตุการณ์จริง (event_2d.py + event_compare.py) สำหรับหน้าเว็บ

  python pipeline/events_web.py --site . --s1 ../s1_hist [--tag b10c400h] [--years 2021 2022 2025]

→ data/static/hotspots/thatako_events.json   (สถิติรายปี รายวัน รายตำบล ระดับน้ำสูงสุด พารามิเตอร์)
→ data/static/hotspots/ev/thatako_ev_<ปี>.png  (แผนที่เทียบ ท่วม ≥ 30 วัน: ตรงกัน / จริงแต่จำลองไม่ท่วม / จำลองเกิน)
"""
import argparse, json, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s1_history as S  # noqa: E402
import event_compare as C  # noqa: E402

COL = {"hit": (37, 99, 235, 230), "miss": (234, 88, 12, 230), "false": (250, 204, 21, 200), "perm": (148, 163, 184, 170)}


def year_map(site, s1_dir, run_npz, out_png, thr=0.3, hid="thatako_ev"):
    from PIL import Image
    r = np.load(run_npz); dates = [str(x) for x in r["snap_dates"]]
    obs, perm = C.s1_masks(s1_dir, int(dates[0][:4]), dates)
    ri, ci = C.lookup(site, hid); w = C.weights(dates)
    dur_o = np.zeros(perm.shape, np.float32); dur_m = np.zeros(perm.shape, np.float32)
    for k, d in enumerate(dates):
        dur_m += ((r["snaps"][k].astype(np.float32)[ri, ci] >= thr) & ~perm) * w[k]
        dur_o += obs[d] * w[k]
    mo, ob = dur_m >= 30, dur_o >= 30
    rgba = np.zeros(perm.shape + (4,), np.uint8)
    rgba[mo & ~ob] = COL["false"]; rgba[ob & ~mo] = COL["miss"]; rgba[mo & ob] = COL["hit"]; rgba[perm] = COL["perm"]
    Image.fromarray(rgba, "RGBA").resize((750, 750), Image.NEAREST).save(out_png, optimize=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="."); ap.add_argument("--s1", default=None)
    ap.add_argument("--tag", default="b10c400h"); ap.add_argument("--years", nargs="+", default=["2021", "2022", "2025"])
    a = ap.parse_args()
    runs = os.path.join(a.site, "hecras", "thatako_ev", "runs")
    hs = os.path.join(a.site, "data", "static", "hotspots"); od = os.path.join(hs, "ev"); os.makedirs(od, exist_ok=True)
    out = {"_bounds": [[S.BBOX[1], S.BBOX[0]], [S.BBOX[3], S.BBOX[2]]], "_tag": a.tag, "_thr_m": 0.3,
           "_legend": [["#2563eb", "ท่วม ≥ 30 วัน ตรงกัน"], ["#ea580c", "ท่วมจริง แบบจำลองไม่ถึง"], ["#facc15", "แบบจำลองท่วมเกิน"], ["#94a3b8", "แหล่งน้ำถาวร"]],
           "years": {}}
    for y in a.years:
        run = json.load(open(os.path.join(runs, f"{y}_{a.tag}.json"), encoding="utf8"))
        cmp_ = json.load(open(os.path.join(runs, f"{y}_{a.tag}_cmp30.json"), encoding="utf8"))
        e = {"start": run["start"], "end": run["end"], "wl_max": run.get("wl_max"),
             "params": {k: run.get(k) for k in ("bank_off", "in_cap_m3s", "hold_level", "loss_mmh", "n_mult")},
             "csi_daily_mean": cmp_["csi_daily_mean"], "ge30d": cmp_["ge30d"],
             "daily": [{k: d[k] for k in ("date", "obs_km2", "mod_km2", "CSI")} for d in cmp_["daily"]],
             "tambon": cmp_["tambon"], "png": None}
        if a.s1:
            fn = f"thatako_ev_{y}.png"; year_map(a.site, a.s1, os.path.join(runs, f"{y}_{a.tag}.npz"), os.path.join(od, fn)); e["png"] = fn
        out["years"][y] = e
        print(y, "CSI", e["csi_daily_mean"], e["ge30d"])
    json.dump(out, open(os.path.join(hs, "thatako_events.json"), "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
