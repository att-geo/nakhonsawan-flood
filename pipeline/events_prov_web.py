# -*- coding: utf-8 -*-
"""
สรุปผลแบบจำลองเหตุการณ์ทั้งจังหวัด (event_2d.py --hid prov_ev) เทียบ Sentinel-1 ขึ้นเว็บ
  python pipeline/events_prov_web.py --site . [--runs hecras/prov_ev/runs] [--tag ""]
อ่าน <ปี><tag>.json (สรุปการจำลอง) + <ปี><tag>_cmp.json/.png (event_compare_prov.py)
-> data/static/prov_events.json + data/static/ev_prov/prov_ev_<ปี>.png
"""
import argparse, json, os, shutil


def main(site, runs=None, tag="", years=(2021, 2022, 2025)):
    runs = runs or os.path.join(site, "hecras", "prov_ev", "runs")
    st = os.path.join(site, "data", "static"); od = os.path.join(st, "ev_prov"); os.makedirs(od, exist_ok=True)
    S = json.load(open(os.path.join(st, "s1_province.json"), encoding="utf8"))
    out = {"bounds": S["bounds"], "years": {}, "tambon": {}, "params": None,
           "note": "แบบจำลอง 2D ทั้งจังหวัด cell 300 ม. (local inertial) ฝน ERA5 + ระดับน้ำสถานีกรมชลประทาน 11 สถานี ; "
                   "เทียบภาพเรดาร์ Sentinel-1 วงโคจร 62+172 วันเดียวกัน (น้ำลึก ≥ 0.3 ม.) ; ท่วม ≥ 30 วัน คิดจากภาพชุดเดียวกันทั้งสองฝั่ง"}
    for y in years:
        base = os.path.join(runs, f"{y}{tag}")
        if not os.path.exists(base + "_cmp.json"):
            continue
        c = json.load(open(base + "_cmp.json", encoding="utf8")); r = json.load(open(base + ".json", encoding="utf8"))
        png = f"prov_ev_{y}.png"; shutil.copy(base + "_cmp.png", os.path.join(od, png))
        out["params"] = {k: r.get(k) for k in ("bank_off", "loss_mmh", "n_mult", "hold_level", "in_cap_m3s", "stations")}
        out["years"][str(y)] = {"png": f"ev_prov/{png}", "n_img": c["n_img"], "daily_csi_mean": c["daily_csi_mean"], "ge30d": c["ge30d"],
                                "district": c["district"], "runtime_s": r.get("runtime_s"),
                                "images": [[x["key"], x["obs_km2"], x["mod_km2"], x["csi"]] for x in c["images"]]}
        for t in c["tambon"]:
            k = f"{t['name']}|{t['district']}"
            out["tambon"].setdefault(k, {"name": t["name"], "district": t["district"], "by_year": {}})
            out["tambon"][k]["by_year"][str(y)] = {kk: t[kk] for kk in ("obs_km2", "mod_km2", "pod", "far", "csi")}
    out["tambon"] = sorted(out["tambon"].values(), key=lambda t: -max([v["obs_km2"] for v in t["by_year"].values()] or [0]))
    json.dump(out, open(os.path.join(st, "prov_events.json"), "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="."); ap.add_argument("--runs", default=None); ap.add_argument("--tag", default="")
    a = ap.parse_args()
    o = main(a.site, a.runs, a.tag)
    for y, v in o["years"].items():
        print(y, v["ge30d"], v["daily_csi_mean"])
