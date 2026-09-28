# -*- coding: utf-8 -*-
"""
Near-realtime update: ดึงข้อมูล -> รันโมเดล -> เขียน data/live/*.json

  python pipeline/run_update.py --site .

แหล่งข้อมูล
  - Open-Meteo  : ฝนรายชั่วโมง ย้อนหลัง 30 วัน + พยากรณ์ 3 วัน (grid 0.1°, ไม่ต้องใช้ key)
  - ThaiWater   : ฝน 24 ชม. จากสถานีโทรมาตร (ใช้ปรับแก้ฝนแบบจำลอง) + ระดับน้ำ/ตลิ่ง (น้ำล้นตลิ่ง)
  - GISTDA      : พื้นที่น้ำท่วมจากดาวเทียม 3 วันล่าสุด (ถ้าตั้ง env GISTDA_API_KEY)
ใช้แค่ stdlib + numpy จึงรันได้ทั้งใน GitHub Actions และ python ของ ArcGIS Pro
"""
import argparse, json, math, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402

TZ = timezone(timedelta(hours=7))
UA = {"User-Agent": "NakhonSawanFloodWatch/1.0 (+github pages)"}
PAST_DAYS, FC_HOURS, FRAME_STEP, FRAME_PAST_H = 30, 72, 3, 168
TW = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/"


def get_json(url, headers=None, timeout=60, tries=3):
    err = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf8"))
        except Exception as e:           # noqa
            err = e; time.sleep(3 * (k + 1))
    raise err


# ---------------------------------------------------------------- rainfall grid
def fetch_openmeteo(bbox, step=0.1):
    x0, y0, x1, y1 = bbox
    lons = np.round(np.arange(x0, x1 + 1e-9, step), 2); lats = np.round(np.arange(y0, y1 + 1e-9, step), 2)
    pts = [(la, lo) for la in lats for lo in lons]
    series, times = [], None
    for i in range(0, len(pts), 80):
        chunk = pts[i:i + 80]
        q = urllib.parse.urlencode({
            "latitude": ",".join(f"{a}" for a, _ in chunk), "longitude": ",".join(f"{b}" for _, b in chunk),
            "hourly": "precipitation", "past_days": PAST_DAYS, "forecast_days": 4,
            "timezone": "GMT", "timeformat": "unixtime", "models": "best_match"})
        js = get_json("https://api.open-meteo.com/v1/forecast?" + q)
        js = js if isinstance(js, list) else [js]
        for o in js:
            if times is None:
                times = np.array(o["hourly"]["time"], dtype=np.int64)
            series.append([v if v is not None else np.nan for v in o["hourly"]["precipitation"]])
    P = np.array(series, dtype=float).T          # [T, G]
    return times, P, np.array([p[0] for p in pts]), np.array([p[1] for p in pts])


def idw_weights(xq, yq, xs, ys, k=4, power=2.0):
    kx = 111.32 * math.cos(math.radians(15.7))
    d = np.hypot((xq[:, None] - xs[None]) * kx, (yq[:, None] - ys[None]) * 110.57)
    idx = np.argsort(d, 1)[:, :k]
    dd = np.take_along_axis(d, idx, 1)
    w = 1 / np.maximum(dd, 0.5) ** power
    return idx, w / w.sum(1, keepdims=True), dd


# ---------------------------------------------------------------- ThaiWater
def fetch_tw_rain(bbox):
    data = get_json(TW + "rain_24h")["data"]
    out = []
    for d in data:
        st = d.get("station") or {}
        la, lo = st.get("tele_station_lat"), st.get("tele_station_long")
        r = d.get("rain_24h")
        if la is None or lo is None or r is None:
            continue
        if not (bbox[1] - 0.3 <= la <= bbox[3] + 0.3 and bbox[0] - 0.3 <= lo <= bbox[2] + 0.3):
            continue
        if not (0 <= float(r) <= 600):
            continue
        out.append({"name": st.get("tele_station_name", {}).get("th", ""), "code": st.get("tele_station_oldcode"),
                    "lat": la, "lon": lo, "rain_24h": float(r), "rain_1h": d.get("rain_1h"),
                    "time": d.get("rainfall_datetime"),
                    "agency": (d.get("agency") or {}).get("agency_shortname", {}).get("th", ""),
                    "amphoe": (d.get("geocode") or {}).get("amphoe_name", {}).get("th", ""),
                    "province": (d.get("geocode") or {}).get("province_name", {}).get("th", "")})
    return out


def fetch_tw_level(bbox, zref):
    data = get_json(TW + "waterlevel")["data"]
    out = []
    f = lambda v: None if v in (None, "", "-") else float(v)
    for d in data:
        st = d.get("station") or {}
        la, lo = st.get("tele_station_lat"), st.get("tele_station_long")
        if la is None or lo is None:
            continue
        if not (bbox[1] <= la <= bbox[3] and bbox[0] <= lo <= bbox[2]):
            continue
        wl, bank = f(d.get("waterlevel_msl")), f(st.get("min_bank"))
        if wl is None or bank is None or abs(wl - bank) > 30:      # ตัดค่าผิดปกติ
            continue
        prev = f(d.get("waterlevel_msl_previous"))
        code = st.get("tele_station_oldcode")
        out.append({"code": code, "name": st.get("tele_station_name", {}).get("th", ""), "lat": la, "lon": lo,
                    "wl": wl, "bank": bank, "ground": f(st.get("ground_level")), "prev": prev,
                    "trend": None if prev is None else round(wl - prev, 2),
                    "diff": round(wl - bank, 2), "situation": d.get("situation_level"),
                    "storage_pct": f(d.get("storage_percent")), "time": d.get("waterlevel_datetime"),
                    "river": (d.get("river_name") or ""), "agency": (d.get("agency") or {}).get("agency_shortname", {}).get("th", ""),
                    "province": (d.get("geocode") or {}).get("province_name", {}).get("th", ""),
                    "z_ref": (zref.get(code) or {}).get("z_ref")})
    return out


def fetch_gistda(key, out_path):
    url = "https://api-gateway.gistda.or.th/api/2.0/resources/features/flood/3days?pv_idn=60&limit=1000&offset=0"
    js = get_json(url, headers={"API-Key": key}, timeout=90)
    feats = js.get("features", [])
    json.dump({"type": "FeatureCollection", "features": feats}, open(out_path, "w", encoding="utf8"),
              ensure_ascii=False, separators=(",", ":"))
    return len(feats)


# ---------------------------------------------------------------- main
def main(site):
    t0 = time.time()
    st_dir = os.path.join(site, "data", "static"); lv = os.path.join(site, "data", "live")
    os.makedirs(lv, exist_ok=True)
    p = json.load(open(os.path.join(st_dir, "params.json")))
    zref_path = os.path.join(st_dir, "stations_ref.json")
    zref = json.load(open(zref_path, encoding="utf8")) if os.path.exists(zref_path) else {}
    prm = model.Params(p)
    bbox = (math.floor(prm.lon.min() * 10) / 10 - 0.1, math.floor(prm.lat.min() * 10) / 10 - 0.1,
            math.ceil(prm.lon.max() * 10) / 10 + 0.1, math.ceil(prm.lat.max() * 10) / 10 + 0.1)
    src = {}

    # 1) model rainfall
    times, Pg, glat, glon = fetch_openmeteo(bbox)
    Pg = np.nan_to_num(Pg)
    now_utc = int(time.time()) // 3600 * 3600
    i_now = int(np.searchsorted(times, now_utc, "right") - 1)
    i_end = min(len(times) - 1, i_now + FC_HOURS)
    times = times[:i_end + 1]; Pg = Pg[:i_end + 1]
    idx, w, _ = idw_weights(prm.lon, prm.lat, glon, glat)
    P = (Pg[:, idx] * w[None]).sum(2)                      # [T, N]
    src["openmeteo"] = {"ok": True, "points": int(Pg.shape[1]), "hours": int(len(times))}

    # 2) gauge adjustment of the last 24 h
    rain_st, adj_note = [], "ไม่ได้ปรับแก้"
    try:
        rain_st = fetch_tw_rain(bbox)
        good = [s for s in rain_st if s["time"] and
                (datetime.now(TZ) - datetime.strptime(s["time"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)).total_seconds() < 6 * 3600]
        if len(good) >= 3:
            gx = np.array([s["lon"] for s in good]); gy = np.array([s["lat"] for s in good])
            gi, gw, _ = idw_weights(gx, gy, glon, glat)
            m24 = (Pg[i_now - 23:i_now + 1][:, gi] * gw[None]).sum(2).sum(0)
            g24 = np.array([s["rain_24h"] for s in good])
            lr = np.log(np.clip((g24 + 2) / (m24 + 2), 0.2, 5))
            hi, hw, hd = idw_weights(prm.lon, prm.lat, gx, gy, k=min(6, len(good)))
            blend = np.clip(1 - (hd.min(1) - 10) / 30, 0, 1)             # เต็มที่ใน 10 กม., หมดที่ 40 กม.
            ratio = np.exp((lr[hi] * hw).sum(1) * blend)
            P[i_now - 23:i_now + 1] *= ratio[None]
            adj_note = (f"ปรับแก้ฝน 24 ชม.ล่าสุดด้วยสถานีโทรมาตร {len(good)} สถานี "
                        f"(ค่ามัธยฐาน สถานี/แบบจำลอง = {np.median(np.exp(lr)):.2f})")
        src["thaiwater_rain"] = {"ok": True, "stations": len(rain_st), "used": len(good)}
    except Exception as e:
        src["thaiwater_rain"] = {"ok": False, "error": str(e)[:200]}

    # 3) river stage
    wl_st = []
    try:
        wl_st = fetch_tw_level(bbox, zref)
        src["thaiwater_level"] = {"ok": True, "stations": len(wl_st)}
    except Exception as e:
        src["thaiwater_level"] = {"ok": False, "error": str(e)[:200]}
    rdep, backwater, nearest = model.river_depth(prm, wl_st)

    # 4) simulate
    depth_rain = model.simulate(P, prm, backwater)
    depth = np.maximum(depth_rain, rdep[None].astype(np.float32))
    cls = model.depth_class(depth)
    hrs, rem = model.durations(depth_rain, i_now)
    # riverine duration from stage trend (m/h)
    for k, s in enumerate(wl_st):
        sel = (nearest == k) & (rdep >= model.FLOOD_CM)
        if not sel.any():
            continue
        tr = s.get("trend") or 0
        r_h = 999 if tr >= 0 else min(int(s["diff"] / -tr) + 1, 999)
        rem = np.where(sel, np.maximum(rem, r_h), rem)
    d_now = depth[i_now]
    srcflag = (depth_rain[i_now] >= model.FLOOD_CM).astype(int) + 2 * (rdep >= model.FLOOD_CM).astype(int)

    fut = slice(i_now + 1, i_end + 1)
    def win(h): return slice(i_now + 1, min(i_now + 1 + h, i_end + 1))
    status = {
        "n": prm.n,
        "d": np.round(d_now).astype(int).tolist(),
        "c": cls[i_now].astype(int).tolist(),
        "s": srcflag.tolist(),
        "h": hrs.tolist(), "r": rem.tolist(),
        "p24": np.round(P[i_now - 23:i_now + 1].sum(0)).astype(int).tolist(),
        "p72": np.round(P[i_now - 71:i_now + 1].sum(0)).astype(int).tolist(),
        "p7d": np.round(P[i_now - 167:i_now + 1].sum(0)).astype(int).tolist(),
        "f24": np.round(P[win(24)].sum(0)).astype(int).tolist(),
        "f72": np.round(P[fut].sum(0)).astype(int).tolist(),
        "m24": cls[win(24)].max(0).astype(int).tolist(),
        "m72": cls[fut].max(0).astype(int).tolist(),
        "dm72": np.round(depth[fut].max(0)).astype(int).tolist(),
    }
    # animation frames every 3 h: -168 h .. +72 h
    fr_idx = list(range(max(i_now - FRAME_PAST_H, 0), i_end + 1, FRAME_STEP))
    if i_now not in fr_idx:
        fr_idx = sorted(set(fr_idx + [i_now]))
    frames = {"t": [int(times[i]) for i in fr_idx], "now": int(times[i_now]),
              "c": ["".join(map(str, cls[i].tolist())) for i in fr_idx]}

    # district summary
    amph = np.asarray(p["amph"]); names = p["amphoe_list"]; hk = p.get("hex_km2", 1.0)
    dist = []
    for a, nm in enumerate(names):
        sel = amph == a
        if not sel.any():
            continue
        c_now = cls[i_now][sel]; c72 = np.asarray(status["m72"])[sel]
        dist.append({"amphoe": nm, "hex": int(sel.sum()),
                     "km2_now": round(float((c_now >= 2).sum() * hk), 1),
                     "km2_watch": round(float((c_now >= 1).sum() * hk), 1),
                     "km2_72h": round(float((c72 >= 2).sum() * hk), 1),
                     "max_cls_now": int(c_now.max()), "max_cls_72h": int(c72.max()),
                     "rain24": round(float(P[i_now - 23:i_now + 1, sel].sum(0).mean()), 1),
                     "rain72": round(float(P[i_now - 71:i_now + 1, sel].sum(0).mean()), 1),
                     "fc72": round(float(P[fut][:, sel].sum(0).mean()), 1)})
    dist.sort(key=lambda d: (-d["max_cls_now"], -d["km2_now"], -d["km2_72h"]))

    # province-level hourly rainfall series for chart
    series = {"t": [int(x) for x in times[max(i_now - FRAME_PAST_H, 0):]],
              "mean": np.round(P[max(i_now - FRAME_PAST_H, 0):].mean(1), 2).tolist(),
              "max": np.round(P[max(i_now - FRAME_PAST_H, 0):].max(1), 2).tolist(),
              "now": int(times[i_now])}

    # GISTDA (optional)
    key = os.environ.get("GISTDA_API_KEY", "").strip()
    if key:
        try:
            src["gistda"] = {"ok": True, "features": fetch_gistda(key, os.path.join(lv, "gistda_flood.geojson"))}
        except Exception as e:
            src["gistda"] = {"ok": False, "error": str(e)[:200]}
    else:
        src["gistda"] = {"ok": False, "error": "ไม่ได้ตั้ง GISTDA_API_KEY"}

    gen = datetime.now(TZ)
    meta = {"generated": gen.isoformat(timespec="seconds"), "now": int(times[i_now]),
            "model": "ponding-cascade v1 (SCS-CN + hex routing + HAND riverine)", "adjust": adj_note,
            "sources": src, "runtime_s": round(time.time() - t0, 1),
            "classes": {"cm": list(model.CLASS_CM), "labels": ["ไม่ท่วม", "เฝ้าระวัง", "ท่วมขังเล็กน้อย", "ท่วมขังปานกลาง", "ท่วมสูง"]},
            "water_hex": int(prm.is_water.sum()),
            "summary": {"km2_now": round(float((cls[i_now] >= 2).sum() * hk), 1),
                        "km2_watch": round(float((cls[i_now] >= 1).sum() * hk), 1),
                        "km2_72h": round(float((np.asarray(status['m72']) >= 2).sum() * hk), 1),
                        "rain24_max": int(max(status["p24"])), "fc72_max": int(max(status["f72"])),
                        "wl_over_bank": sum(1 for s in wl_st if s["diff"] > 0 and s.get("province") == "นครสวรรค์")}}
    W = lambda n, o: json.dump(o, open(os.path.join(lv, n), "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))
    W("status.json", status); W("frames.json", frames); W("districts.json", dist); W("series.json", series)
    W("stations.json", {"rain": rain_st, "level": wl_st})
    W("meta.json", meta)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=".")
    main(ap.parse_args().site)
