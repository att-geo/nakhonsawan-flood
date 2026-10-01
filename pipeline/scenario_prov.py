# -*- coding: utf-8 -*-
"""
สถานการณ์สมมติทั้งจังหวัด (โดเมน prov_ev 300 ม.) — แบบเดียวกับสถานการณ์ A–E ของแอ่งท่าตะโก (scenario_2d.py)
แต่ใช้ชุดพารามิเตอร์ที่สอบเทียบกับ Sentinel-1 ทั้งจังหวัด (event_2d.py รอบ 5: ตลิ่งใช้งาน/น้ำเข้าทุ่งรายลำน้ำ, ความจุบึงบอระเพ็ด)

  python pipeline/scenario_prov.py --site . [--only C D E] [--days 21]

ฝนสม่ำเสมอทั้งโดเมน (รูปแบบรายชั่วโมงเดียวกับ scenario_2d.rain_pattern) ; ระดับน้ำแม่น้ำต่อสถานี:
  low  : ความลึก 40% ของตลิ่ง (ระบายได้ปกติ)
  bank : เต็มตลิ่ง (ตลิ่ง − 0.3 ม.) 10 วัน แล้วลดถึง low ใน 7 วัน
         * ตลิ่งใช้งานของแบบจำลองต่ำกว่าตลิ่งจริง 1–2 ม. (น้ำเข้าทุ่งผ่านคลอง/ประตูน้ำ) จึงมีน้ำเข้าทุ่งได้ตามความจุรายลำน้ำ
  over : ล้นตลิ่ง +0.5 ม. 7 วัน แล้วลดถึง low ใน 7 วัน
ระดับบึงบอระเพ็ดเริ่มต้น +24.00 ม. (ระดับเก็บกักปกติ ชป. — สถานการณ์พายุกลางฤดูฝน) ; พื้นดินเริ่มแห้ง คันนาเริ่มครึ่งหนึ่ง
ผล: data/static/prov_scenarios.json (สรุปรายตำบล/อำเภอ) + data/static/scn_prov/prov_scn_<ชื่อ>_{max,rem}.png
"""
import argparse, json, os, sys, time
from datetime import datetime, timezone, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import event_2d, model2d  # noqa: E402
from scenario_2d import rain_pattern  # noqa: E402

SCEN = {
    "A": dict(rain=150, rain_days=3, river="low", label="ฝน 150 มม./3 วัน · แม่น้ำปกติ"),
    "C": dict(rain=250, rain_days=5, river="low", label="ฝน 250 มม./5 วัน · แม่น้ำปกติ"),
    "D": dict(rain=250, rain_days=5, river="bank", label="ฝน 250 มม./5 วัน · แม่น้ำเต็มตลิ่ง 10 วัน"),
    "E": dict(rain=250, rain_days=5, river="over", label="ฝน 250 มม./5 วัน · แม่น้ำล้นตลิ่ง +0.5 ม. 7 วัน"),
}
PARAMS = dict(bank_off={"nan": -1.5, "ping": -1.5, "cpy": -2.0, "skg": -1.0}, loss=0.08, n_mult=2.0, hold_level=24.0,
              in_cap={"nan": 500, "ping": 1000, "cpy": 1000, "skg": 300}, bueng=True, bueng_init=24.0)
YEAR = 2030                                   # วันที่สมมติ (ไม่ขึ้นกับปีจริง)
TZ7 = timezone(timedelta(hours=7))


def stage_rows(z_ref, bank, kind, T, t0):
    low = z_ref + 0.4 * (bank - z_ref); full = bank - 0.3; over = bank + 0.5
    hrs = np.arange(T)
    if kind == "low":
        wl = np.full(T, low)
    elif kind == "bank":
        f = np.clip((hrs - 240) / 168.0, 0, 1); wl = full + (low - full) * f
    else:
        f = np.clip((hrs - 168) / 168.0, 0, 1); wl = over + (low - over) * f
    return [[datetime.fromtimestamp(t0 + 3600 * h, TZ7).strftime("%Y-%m-%d %H:%M"), round(float(v), 3), None] for h, v in zip(hrs, wl)]


def forcing_for(site, scn, days):
    base = json.load(open(os.path.join(site, "hecras", "prov_ev", "forcing", "2025.json")))     # พิกัด lattice + ข้อมูลสถานี (ตลิ่ง)
    d = np.load(os.path.join(site, "data", "static", "hotspots", "prov_ev.npz")); m = json.loads(str(d["meta"]))
    zref = {s["code"]: s["z_ref"] for s in m["stations"]}
    t0 = datetime(YEAR, 9, 1, tzinfo=timezone.utc).timestamp()
    T = (days + 2) * 24
    warm = 31 * 24                                                       # ข้อมูลฝนเริ่ม 1 ส.ค. (event_2d อุ่นเครื่องจากต้นข้อมูล) — ไม่มีฝน
    P = np.concatenate([np.zeros(warm, np.float32), rain_pattern(scn["rain"], scn["rain_days"], T)])
    R = {"lat": base["rain"]["lat"], "lon": base["rain"]["lon"], "t0_utc": f"{YEAR}-08-01T00:00",
         "P": np.repeat(P[None], len(base["rain"]["lat"]), 0).round(2).tolist()}
    st = {}
    sm = base["station_meta"]
    for c, e in sm.items():
        if not e.get("min_bank"):
            continue
        z = zref.get(c)
        if z is None:                                                    # สถานีที่ไม่อยู่ใน meta (C.13, PIN005): ใช้ z_ref จากข้อมูลจริงปีนั้น
            rows = base["stations"].get(c) or []
            if not rows:
                continue
            z = float(np.percentile([r[1] for r in rows], 1)) - 0.5
        st[c] = stage_rows(z, float(e["min_bank"]), scn["river"], T, t0 - 7 * 3600)
    return {"rain": R, "stations": st, "station_meta": sm}


def main(site, only=None, days=21, post_only=False):
    st_dir = os.path.join(site, "data", "static"); od = os.path.join(st_dir, "scn_prov"); os.makedirs(od, exist_ok=True)
    rd = os.path.join(site, "hecras", "prov_ev", "scenarios"); os.makedirs(rd, exist_ok=True)
    rp = os.path.join(st_dir, "prov_scenarios.json")
    res = json.load(open(rp, encoding="utf8")) if os.path.exists(rp) else {}
    d = np.load(os.path.join(st_dir, "hotspots", "prov_ev.npz")); dom = {k: d[k] for k in d.files if k != "meta"}
    m = json.loads(str(d["meta"]))
    for name, scn in SCEN.items():
        if only and name not in only:
            continue
        t = time.time()
        end = (datetime(YEAR, 9, 1) + timedelta(days=days)).strftime("%m-%d")
        if not post_only:
            F = forcing_for(site, scn, days)
            event_2d.run(site, YEAR, start="09-01", end=end, tag=f"scn_{name}", hid="prov_ev", forcing=F, out_dir=rd, north=True, **PARAMS)
        r = np.load(os.path.join(rd, f"{YEAR}scn_{name}.npz"))
        # สรุประยะเวลาท่วมขังรายตำบล — ผืนบึงบอระเพ็ด (ปรับท้องบึง) และแหล่งน้ำถาวรไม่นับ
        from run_hotspots import _ponding
        ny, nx = dom["dem"].shape; (sw, ne) = m["bounds"]
        LA, LO = np.meshgrid(np.linspace(ne[0], sw[0], ny), np.linspace(sw[1], ne[1], nx), indexing="ij")
        fs = [event_2d.poly_mask(json.load(open(os.path.join(st_dir, "hotspots", f_), encoding="utf8"))["features"], LA, LO)
              for f_ in ("thatako_contrib.geojson", "thatako_aoi.geojson")]
        _, lake = event_2d.burn_boraphet(dom["dem"].astype(np.float32), (fs[0] | fs[1]) & (dom["river"] < 0), LA, LO, float(m["dx"]), log=lambda *a: None)
        domp = dict(dom); domp["n"] = np.where(lake, np.float32(0.03), dom["n"]).astype(np.float32)
        T = days * 24 + 1
        rr_ = {"wet_h": r["wet_h"].astype(np.float32), "hmax": r["hmax"].astype(np.float32), "last_wet": r["last_wet"].astype(np.float32), "h": r["h_end"].astype(np.float32),
               "zseries": r["zseries"], "snaps": {0: r["hmax"].astype(np.float32)}}
        pd, sig = _ponding(m, domp, rr_, 1, 0, T, None, m["dx"])
        summ = {"ponding": pd}
        hmax = r["hmax"].astype(np.float32); last = r["last_wet"].astype(np.float32); hend = r["h_end"].astype(np.float32)
        sig = sig & (dom["aoi"] >= 0)                                    # แสดงเฉพาะในเขตจังหวัด
        model2d.depth_png(os.path.join(od, f"prov_scn_{name}_max.png"), np.where(sig, hmax, 0), dom["river"])
        model2d.remain_png(os.path.join(od, f"prov_scn_{name}_rem.png"), np.where(sig, last, -1), (hend >= 0.10) & sig, dom["river"])
        pd = summ["ponding"]
        keep = ("code", "name", "district", "area_km2", "patch_km2", "wet_ge7d_km2", "depth_p95_m")
        rows = [{**{k: x.get(k) for k in keep}, "left_end_km2": x.get("wet_ge14d_km2")} for x in pd["tambon"]]
        dist = {}
        for x in rows:
            g = dist.setdefault(x["district"], {"patch_km2": 0.0, "ge7d_km2": 0.0, "left_end_km2": 0.0})
            g["patch_km2"] += x["patch_km2"] or 0; g["ge7d_km2"] += x["wet_ge7d_km2"] or 0
            g["left_end_km2"] += x["left_end_km2"] or 0          # _ponding: wet_ge14d_km2 = ยังท่วมเมื่อจบการจำลอง
        tot = pd["total_new"]
        res[name] = {**scn, "days": days, "png": {"max": f"scn_prov/prov_scn_{name}_max.png", "rem": f"scn_prov/prov_scn_{name}_rem.png"},
                     "total": {"patch_km2": tot["patch_km2"], "peak_km2": tot["wet_peak_km2"], "ge7d_km2": tot["wet_ge7d_km2"],
                               "left_end_km2": tot["wet_ge14d_km2"]},
                     "tambon": rows, "district": {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in dist.items()},
                     "runtime_s": round(time.time() - t, 1)}
        print(name, res[name]["total"], res[name]["runtime_s"], flush=True)
        cur = json.load(open(rp, encoding="utf8")) if os.path.exists(rp) else {}
        cur.update({name: res[name], "_bounds": m["bounds"], "_params": {k: v for k, v in PARAMS.items()},
                    "_note": "สถานการณ์สมมติ ไม่ใช่พยากรณ์ ; ระดับบึงบอระเพ็ดเริ่ม +24.00 ม. ; พื้นดินเริ่มแห้ง คันนาเริ่มครึ่งหนึ่ง ; จำลอง 21 วัน"})
        json.dump(cur, open(rp, "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="."); ap.add_argument("--only", nargs="*"); ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--post", action="store_true", help="สรุปผลใหม่จาก npz ที่มีอยู่ (ไม่รันแบบจำลอง)")
    a = ap.parse_args()
    main(a.site, a.only, a.days, a.post)
