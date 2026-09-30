# -*- coding: utf-8 -*-
"""
สถานการณ์จำลอง (scenario) 2D สำหรับวิเคราะห์การท่วมขังซ้ำซาก/นาน — ไม่ขึ้นกับฝนหรือระดับน้ำวันนี้

  python pipeline/scenario_2d.py --site . --domain thatako [--rain 150 --rain-days 3 --days 21]

แต่ละสถานการณ์: ฝนสม่ำเสมอทั้งโดเมน (มม. รวม / จำนวนวัน, รูปแบบรายชั่วโมงแบบ Huff ง่าย ๆ) -> SCS-CN + คันนา
และระดับน้ำแม่น้ำ 2 แบบ เพื่อแยกผลของ "น้ำเท้อจากแม่น้ำ" ออกจาก "ฝนตกในพื้นที่":
  low  : ความลึกน้ำในแม่น้ำ = 40% ของความสูงตลิ่ง (ระบายได้ปกติ) ตลอดช่วง
  bank : แม่น้ำเต็มตลิ่ง (ตลิ่ง − 0.3 ม.) คงที่ hold_days วัน แล้วลดเป็นเส้นตรงถึงระดับ low ใน 7 วัน
         (ไม่ล้นตลิ่ง — ผลที่ได้คือพื้นที่ที่ระบายไม่ได้เพราะแม่น้ำสูง)
  over : แม่น้ำล้นตลิ่ง +over_m ม. 7 วัน แล้วลดถึงระดับ low ใน 7 วัน โดยปริมาณน้ำล้นรวมทั้งโดเมนไม่เกิน over_q ลบ.ม./วิ
         (7 วันแรก แล้วลดเป็นศูนย์ใน 7 วัน) — ถ้าไม่จำกัดปริมาณ น้ำจะเติมที่ลุ่มจนระดับเท่าแม่น้ำ (bathtub) ซึ่งเกินจริงมาก
เงื่อนไขเริ่มต้น: แห้ง (ไม่มีน้ำผิวดิน) คันนาเก็บได้ 100 มม. เริ่มที่ครึ่งหนึ่ง
ผล: data/static/hotspots/<domain>_scenarios.json (สรุปรายตำบล) + data/static/hotspots/scn/<domain>_scn_<name>_{max,rem}.png
    + hecras/<domain>/scenarios/scn_<name>.npz (ความลึกสูงสุด/ระยะเวลาราย cell)
"""
import argparse, json, os, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model2d  # noqa: E402
from run_hotspots import _runoff, _ponding  # noqa: E402


def rain_pattern(total_mm, days, hours_total):
    """ฝนรายชั่วโมง: แต่ละวันตก 12 ชม. (14:00–02:00) รูประฆังคว่ำ, วันแรก 40% วันถัดไปเฉลี่ยส่วนที่เหลือ"""
    P = np.zeros(hours_total, np.float32)
    if days <= 0 or total_mm <= 0:
        return P
    w_day = np.array([0.4] + [0.6 / (days - 1)] * (days - 1)) if days > 1 else np.array([1.0])
    bell = np.sin(np.linspace(0, np.pi, 14)[1:-1]); bell /= bell.sum()
    for d in range(days):
        s = 24 * d + 14
        seg = (total_mm * w_day[d] * bell).astype(np.float32)[:max(0, hours_total - s)]
        P[s:s + seg.size] += seg
    return P


def run(site, domain, rain=150.0, rain_days=3, days=21, hold_days=10, only=None, over_m=0.5, tag="", over_q=500.0):
    st_dir = os.path.join(site, "data", "static"); hs_dir = os.path.join(st_dir, "hotspots")
    out_dir = os.path.join(hs_dir, "scn"); os.makedirs(out_dir, exist_ok=True)   # static: workflow ไม่เขียนทับ
    idx = json.load(open(os.path.join(hs_dir, "hotspots.json"), encoding="utf8"))
    m = idx[domain]
    d = np.load(os.path.join(hs_dir, f"{domain}.npz"))
    dom = {k: d[k] for k in d.files if k != "meta"}
    m = json.loads(str(d["meta"]))
    stations = {x["code"]: x for x in json.load(open(os.path.join(site, "data", "live", "stations.json"), encoding="utf8"))["level"]}
    ny, nx = dom["dem"].shape; T = days * 24 + 1
    # น้ำท่า: ฝนสม่ำเสมอ ทุก cell (SCS-CN ต่อ cell + คันนา) — คำนวณเป็นคอลัมน์เดียวต่อค่า (cn, crop) ที่ไม่ซ้ำ เพื่อประหยัดหน่วยความจำ
    P = rain_pattern(rain, rain_days, T)
    cn = dom["cn"].ravel().astype(np.float32); bk = np.where(dom["crop"].ravel(), 100.0, 10.0).astype(np.float32)
    key = cn * 1000 + bk
    uk, inv = np.unique(key, return_inverse=True)
    q_u = _runoff(np.repeat(P[:, None], uk.size, 1), (uk // 1000).astype(np.float32), (uk % 1000).astype(np.float32))
    rain_eff = q_u[:, inv].reshape(T, ny, nx)
    # แม่น้ำ: ความลึกเหนือท้องน้ำราย cell จาก IDW ของความสูงตลิ่งสถานี (bank − z_ref)
    rr, cc = np.where(dom["river"] >= 0)
    (sw, ne) = m["bounds"]; la = np.linspace(ne[0], sw[0], ny); lo = np.linspace(sw[1], ne[1], nx)
    ks = [k for k, s in enumerate(m["stations"]) if stations.get(s["code"], {}).get("bank", -1e9) > s["z_ref"]]
    slat = np.array([m["stations"][k]["lat"] for k in ks]); slon = np.array([m["stations"][k]["lon"] for k in ks])
    dk = np.hypot((lo[cc][:, None] - slon[None]) * 107.1, (la[rr][:, None] - slat[None]) * 110.6)
    nn2 = np.argsort(dk, 1)[:, :min(2, len(ks))]
    wk = 1 / np.maximum(np.take_along_axis(dk, nn2, 1), 0.3) ** 2; wk /= wk.sum(1, keepdims=True)
    BH = np.array([stations[m["stations"][k]["code"]]["bank"] - m["stations"][k]["z_ref"] for k in ks])
    zb = (dom["zbed"] if "zbed" in dom else dom["dem"])[rr, cc].astype(np.float32)
    bank_depth = (BH[nn2] * wk).sum(1); river_bank = zb + bank_depth
    low = zb + 0.4 * bank_depth; full = zb + np.maximum(bank_depth - 0.3, 0.4 * bank_depth)

    def stage_bank(hr):
        if hr < hold_days * 24:
            return full
        f = min((hr - hold_days * 24) / (7 * 24.0), 1.0)
        return full + (low - full) * f
    over = river_bank + over_m

    def stage_over(hr):
        if hr < 7 * 24:
            return over
        f = min((hr - 7 * 24) / (7 * 24.0), 1.0)
        return over + (low - over) * f
    scen = {"low": (lambda hr: low), "bank": stage_bank, "over": stage_over}
    res_path = os.path.join(hs_dir, f"{domain}_scenarios.json")
    res = json.load(open(res_path, encoding="utf8")) if os.path.exists(res_path) else {}
    for name, sfn in scen.items():
        if only and name not in only:
            continue
        t0 = time.time()
        r = model2d.run2d(dom, m, rain_eff, T - 1, sfn, h0=None, snap_hours=(), river_bank=river_bank,
                          overflow_fn=(lambda hr: over_q * min(1.0, max(0.0, (14 * 24 - hr) / (7 * 24.0)))) if name == "over" else (lambda hr: 0.0),
                          dur_from=0, zones=dom["aoi"], n_zones=len(m["aoi"]))
        r["snaps"] = {0: r["hmax"]}                                  # "ตอนนี้" = ความลึกสูงสุด (wet_now = พื้นที่ท่วมสูงสุดที่เป็นผืน)
        pd, sig = _ponding(m, dom, r, 0 + 1, 0, T, None, m["dx"])
        od = os.path.join(site, "hecras", domain, "scenarios"); os.makedirs(od, exist_ok=True)   # ผลราย cell สำหรับวิเคราะห์ซ้ำ (ไม่ใช้บนเว็บ)
        np.savez_compressed(os.path.join(od, f"scn_{name}{tag}.npz"), hmax=r["hmax"].astype(np.float16),
                            h_end=r["h"].astype(np.float16), wet_h=r["wet_h"].astype(np.float16),
                            last_wet=r["last_wet"].astype(np.float32), zseries=r["zseries"])
        still = (r["h"] >= 0.10) & sig
        key = name + tag
        model2d.depth_png(os.path.join(out_dir, f"{domain}_scn_{key}_max.png"), np.where(sig, r["hmax"], 0), dom["river"])
        model2d.remain_png(os.path.join(out_dir, f"{domain}_scn_{key}_rem.png"), np.where(sig, r["last_wet"], -1), still, dom["river"])
        res[key] = {"rain_mm": rain, "rain_days": rain_days, "days": days, "hold_days": hold_days if name == "bank" else 0,
                     "river": {"low": "ความลึก 40% ของตลิ่ง", "bank": f"เต็มตลิ่ง (−0.3 ม.) {hold_days} วัน แล้วลดใน 7 วัน",
                               "over": f"ล้นตลิ่ง +{over_m} ม. 7 วัน แล้วลดใน 7 วัน, น้ำล้นรวม ≤ {over_q:.0f} ลบ.ม./วิ"}[name],
                     "ponding": pd, "png": {"max": f"{domain}_scn_{key}_max.png", "rem": f"{domain}_scn_{key}_rem.png"},
                     "runtime_s": round(time.time() - t0, 1)}
        print(key, pd["total_new"], round(time.time() - t0, 1), "s", flush=True)
    res["_bounds"] = m["bounds"]
    if os.path.exists(res_path):                                     # รวมกับผลที่อาจเขียนโดยโปรเซสอื่นระหว่างนี้
        cur = json.load(open(res_path, encoding="utf8"))
        cur.update({k: v for k, v in res.items() if not only or k[:len(k) - len(tag)] in only or k.startswith("_")}); res = cur
    json.dump(res, open(res_path, "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="."); ap.add_argument("--domain", default="thatako")
    ap.add_argument("--rain", type=float, default=150.0); ap.add_argument("--rain-days", type=int, default=3)
    ap.add_argument("--days", type=int, default=21); ap.add_argument("--hold-days", type=int, default=10)
    ap.add_argument("--only", nargs="*"); ap.add_argument("--over-m", type=float, default=0.5); ap.add_argument("--over-q", type=float, default=500.0)
    ap.add_argument("--tag", default="", help="ต่อท้ายชื่อสถานการณ์ เช่น _r300")
    a = ap.parse_args()
    run(a.site, a.domain, a.rain, a.rain_days, a.days, a.hold_days, a.only, a.over_m, a.tag, a.over_q)
