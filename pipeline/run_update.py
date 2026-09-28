# -*- coding: utf-8 -*-
"""
Near-realtime update: ดึงข้อมูล -> รันโมเดล -> เขียน data/live/*.json

  python pipeline/run_update.py --site .

แหล่งข้อมูล
  - Open-Meteo  : ฝนรายชั่วโมง ย้อนหลัง 30 วัน + พยากรณ์ 3 วัน บนจุด grid 0.2° ครอบคลุม นครสวรรค์ + 4 จังหวัดต้นน้ำ
                  (เก็บ cache ไว้ใน data/live/rain_cache.json รอบถัดไปดึงแค่ 2 วันล่าสุด -> ประหยัดโควตา)
  - ThaiWater   : ฝน 24 ชม. สถานีโทรมาตร (ปรับแก้ฝนแบบจำลอง), ระดับน้ำ/ตลิ่ง/ปริมาณน้ำไหล (RID) ของ 5 จังหวัด
  - GISTDA      : พื้นที่น้ำท่วมจากดาวเทียม 3 วันล่าสุด (ถ้าตั้ง env GISTDA_API_KEY)
ใช้แค่ stdlib + numpy จึงรันได้ทั้งใน GitHub Actions และ python ของ ArcGIS Pro
"""
import argparse, json, math, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402
import upstream as upm  # noqa: E402

TZ = timezone(timedelta(hours=7))
UA = {"User-Agent": "NakhonSawanFloodWatch/1.1 (+github pages)"}
PAST_DAYS, FC_HOURS, FRAME_STEP, FRAME_PAST_H = 30, 72, 3, 168
HOT_PAST_H = 48                                               # 2D hotspot เริ่มจำลองย้อนหลัง 48 ชม. (ใช้สภาพน้ำจากโมเดล hex)
TW = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/"
G_LON0, G_LAT0, G_STEP, G_COLS = 99.0, 15.0, 0.2, 16          # ต้องตรงกับ arcgis/build_upstream.py
REGION = (98.9, 14.9, 101.9, 18.0)                            # ขอบเขตดึงสถานี (5 จังหวัด)
UP_PROV = {"62", "65", "66", "67"}


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


# ---------------------------------------------------------------- rainfall grid (0.2° lattice)
def gid_lonlat(g):
    g = np.asarray(g); j, i = np.divmod(g, G_COLS)
    return np.round(G_LON0 + G_STEP * i, 2), np.round(G_LAT0 + G_STEP * j, 2)


def fetch_openmeteo_pts(gids, past_days):
    lon, lat = gid_lonlat(gids)
    series, times = [], None
    for i in range(0, len(gids), 80):
        q = urllib.parse.urlencode({
            "latitude": ",".join(f"{a}" for a in lat[i:i + 80]), "longitude": ",".join(f"{b}" for b in lon[i:i + 80]),
            "hourly": "precipitation", "past_days": past_days, "forecast_days": 4,
            "timezone": "GMT", "timeformat": "unixtime", "models": "best_match"})
        js = get_json("https://api.open-meteo.com/v1/forecast?" + q)
        js = js if isinstance(js, list) else [js]
        for o in js:
            if times is None:
                times = np.array(o["hourly"]["time"], dtype=np.int64)
            series.append([v if v is not None else np.nan for v in o["hourly"]["precipitation"]])
    return times, np.nan_to_num(np.array(series, dtype=float).T)      # [T,G]


def get_rain(gids, cache_path, now_utc):
    """30 วันย้อนหลัง + พยากรณ์ ใช้ cache: ถ้ามี cache ใหม่พอ ดึงแค่ past_days=2"""
    gids = list(map(int, gids)); mode = "full"
    cache = None
    if os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path))
        except Exception:  # noqa
            cache = None
    if cache and set(gids) <= set(cache["gids"]) and cache["t"][-1] >= now_utc and \
            now_utc - cache.get("fetched", 0) < 36 * 3600 and cache["t"][0] <= now_utc - (PAST_DAYS - 1) * 86400:
        t_new, P_new = fetch_openmeteo_pts(gids, 2)
        ct = np.array(cache["t"], np.int64); cP = np.array(cache["P"], float)[:, [cache["gids"].index(g) for g in gids]] / 10
        t0 = now_utc - PAST_DAYS * 86400
        times = np.arange(max(ct[0], t0), t_new[-1] + 1, 3600, dtype=np.int64)
        P = np.zeros((times.size, len(gids)))
        m = np.isin(times, ct); P[m] = cP[np.searchsorted(ct, times[m])]
        m2 = np.isin(times, t_new); P[m2] = P_new[np.searchsorted(t_new, times[m2])]
        mode = "incremental"
    else:
        times, P = fetch_openmeteo_pts(gids, PAST_DAYS)
    json.dump({"gids": gids, "fetched": now_utc, "t": times.tolist(), "P": np.round(P * 10).astype(int).tolist()},
              open(cache_path, "w"), separators=(",", ":"))
    return times, P, mode


def idw_weights(xq, yq, xs, ys, k=4, power=2.0):
    d = np.hypot((xq[:, None] - xs[None]) * upm.KX, (yq[:, None] - ys[None]) * upm.KY)
    idx = np.argsort(d, 1)[:, :k]
    dd = np.take_along_axis(d, idx, 1)
    w = 1 / np.maximum(dd, 0.5) ** power
    return idx, w / w.sum(1, keepdims=True), dd


def gauge_adjust(P, i_now, tlon, tlat, good, glon_grid, glat_grid, Pg):
    """คูณฝน 24 ชม.ล่าสุดของเป้าหมายด้วยอัตราส่วน สถานี/แบบจำลอง (IDW ใน log-space, blend ตามระยะ)"""
    gx = np.array([s["lon"] for s in good]); gy = np.array([s["lat"] for s in good])
    gi, gw, _ = idw_weights(gx, gy, glon_grid, glat_grid)
    m24 = (Pg[i_now - 23:i_now + 1][:, gi] * gw[None]).sum(2).sum(0)
    g24 = np.array([s["rain_24h"] for s in good])
    lr = np.log(np.clip((g24 + 2) / (m24 + 2), 0.2, 5))
    hi, hw, hd = idw_weights(tlon, tlat, gx, gy, k=min(6, len(good)))
    blend = np.clip(1 - (hd.min(1) - 10) / 30, 0, 1)
    P[i_now - 23:i_now + 1] *= np.exp((lr[hi] * hw).sum(1) * blend)[None]
    return float(np.median(np.exp(lr)))


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
        if not (bbox[1] <= la <= bbox[3] and bbox[0] <= lo <= bbox[2]) or not (0 <= float(r) <= 600):
            continue
        out.append({"name": st.get("tele_station_name", {}).get("th", ""), "code": st.get("tele_station_oldcode"),
                    "lat": la, "lon": lo, "rain_24h": float(r), "rain_1h": d.get("rain_1h"),
                    "time": d.get("rainfall_datetime"),
                    "agency": (d.get("agency") or {}).get("agency_shortname", {}).get("th", ""),
                    "amphoe": (d.get("geocode") or {}).get("amphoe_name", {}).get("th", ""),
                    "province": (d.get("geocode") or {}).get("province_name", {}).get("th", ""),
                    "pcode": (d.get("geocode") or {}).get("province_code", "")})
    return out


def fetch_tw_level(bbox, zref):
    data = get_json(TW + "waterlevel")["data"]
    out = []
    f = lambda v: None if v in (None, "", "-") else float(v)
    for d in data:
        st = d.get("station") or {}
        la, lo = st.get("tele_station_lat"), st.get("tele_station_long")
        if la is None or lo is None or not (bbox[1] <= la <= bbox[3] and bbox[0] <= lo <= bbox[2]):
            continue
        wl, bank = f(d.get("waterlevel_msl")), f(st.get("min_bank"))
        if wl is None or bank is None or abs(wl - bank) > 30:
            continue
        prev = f(d.get("waterlevel_msl_previous")); code = st.get("tele_station_oldcode")
        geo = d.get("geocode") or {}
        out.append({"code": code, "name": st.get("tele_station_name", {}).get("th", ""), "lat": la, "lon": lo,
                    "wl": wl, "bank": bank, "ground": f(st.get("ground_level")), "prev": prev,
                    "trend": None if prev is None else round(wl - prev, 2),
                    "diff": round(wl - bank, 2), "situation": d.get("situation_level"),
                    "storage_pct": f(d.get("storage_percent")), "time": d.get("waterlevel_datetime"),
                    "q": f(d.get("discharge")), "qmax": f(st.get("qmax")),
                    "river": (d.get("river_name") or ""), "agency": (d.get("agency") or {}).get("agency_shortname", {}).get("th", ""),
                    "province": geo.get("province_name", {}).get("th", ""), "pcode": geo.get("province_code", ""),
                    "z_ref": (zref.get(code) or {}).get("z_ref")})
        o = out[-1]
        if o["q"] is None and o["qmax"] and o["storage_pct"]:
            # ไม่มีค่า Q รอบนี้: ประมาณจากความจุลำน้ำ Q ≈ qmax·(ระดับน้ำ/ความลึกตลิ่ง)^(5/3) (Manning)
            o["q_est"] = round(o["qmax"] * (max(o["storage_pct"], 0) / 100.0) ** (5 / 3), 1)
    return out


def fetch_gistda(key, out_path):
    url = "https://api-gateway.gistda.or.th/api/2.0/resources/features/flood/3days?pv_idn=60&limit=1000&offset=0"
    js = get_json(url, headers={"API-Key": key}, timeout=90)
    feats = js.get("features", [])
    json.dump({"type": "FeatureCollection", "features": feats}, open(out_path, "w", encoding="utf8"),
              ensure_ascii=False, separators=(",", ":"))
    return len(feats)


def update_gauge_history(path, stations, keep_days=10):
    hist = {}
    if os.path.exists(path):
        try:
            hist = json.load(open(path, encoding="utf8"))
        except Exception:  # noqa
            hist = {}
    cut = time.time() - keep_days * 86400
    for s in stations:
        if not s.get("time"):
            continue
        t = int(datetime.strptime(s["time"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp())
        h = [r for r in hist.get(s["code"], []) if r[0] >= cut and r[0] != t]
        h.append([t, s.get("q"), s["wl"]]); h.sort()
        hist[s["code"]] = h
    json.dump(hist, open(path, "w", encoding="utf8"), separators=(",", ":"))
    return hist


# ---------------------------------------------------------------- main
def main(site):
    t0 = time.time()
    st_dir = os.path.join(site, "data", "static"); lv = os.path.join(site, "data", "live")
    os.makedirs(lv, exist_ok=True)
    p = json.load(open(os.path.join(st_dir, "params.json")))
    J = lambda n: json.load(open(os.path.join(st_dir, n), encoding="utf8")) if os.path.exists(os.path.join(st_dir, n)) else None
    zref = J("stations_ref.json") or {}
    zones, entries = J("upstream_zones.json"), J("upstream_entries.json")
    prm = model.Params(p)
    hk = p.get("hex_km2", 1.0)
    src = {}
    now_utc = int(time.time()) // 3600 * 3600

    # 1) rainfall on the 0.2° lattice (NS + upstream zones)
    lat_ax = np.arange(G_LAT0, 18.21, G_STEP); lon_ax = np.arange(G_LON0, G_LON0 + G_STEP * G_COLS - 1e-9, G_STEP)
    gl = [(j, i) for j, la in enumerate(lat_ax) for i, lo in enumerate(lon_ax)
          if prm.lat.min() - 0.25 <= la <= prm.lat.max() + 0.25 and prm.lon.min() - 0.25 <= lo <= prm.lon.max() + 0.25]
    ns_gids = [j * G_COLS + i for j, i in gl]
    z_gids = sorted(set(int(g) for g in zones["gid"])) if zones else []
    gids = sorted(set(ns_gids) | set(z_gids))
    times, Pg, rmode = get_rain(gids, os.path.join(lv, "rain_cache.json"), now_utc)
    glon, glat = gid_lonlat(gids)
    i_now = int(np.searchsorted(times, now_utc, "right") - 1)
    i_end = min(len(times) - 1, i_now + FC_HOURS)
    times = times[:i_end + 1]; Pg = Pg[:i_end + 1]
    T = len(times)
    ns_cols = np.array([gids.index(g) for g in ns_gids])
    idx, w, _ = idw_weights(prm.lon, prm.lat, glon[ns_cols], glat[ns_cols])
    P = (Pg[:, ns_cols][:, idx] * w[None]).sum(2)                      # [T, N]
    src["openmeteo"] = {"ok": True, "points": len(gids), "hours": T, "mode": rmode}
    Pz = None
    if zones:
        zg = np.array(zones["gid"]); zcol = np.searchsorted(np.array(gids), zg)
        Pz = Pg[:, zcol].copy()                                          # [T, Z]
        zlon, zlat = gid_lonlat(zg)

    # 2) gauge adjustment (last 24 h) — NS hexes and upstream zones
    rain_st, adj_note = [], "ไม่ได้ปรับแก้"
    try:
        rain_st = fetch_tw_rain(REGION)
        good = [s for s in rain_st if s["time"] and (datetime.now(TZ) - datetime.strptime(s["time"][:16], "%Y-%m-%d %H:%M")
                                                       .replace(tzinfo=TZ)).total_seconds() < 6 * 3600]
        if len(good) >= 3:
            med = gauge_adjust(P, i_now, prm.lon, prm.lat, good, glon, glat, Pg)
            if Pz is not None:
                gauge_adjust(Pz, i_now, zlon, zlat, good, glon, glat, Pg)
            adj_note = f"ปรับแก้ฝน 24 ชม.ล่าสุดด้วยสถานีโทรมาตร {len(good)} สถานี (ค่ามัธยฐาน สถานี/แบบจำลอง = {med:.2f})"
        src["thaiwater_rain"] = {"ok": True, "stations": len(rain_st), "used": len(good)}
    except Exception as e:
        src["thaiwater_rain"] = {"ok": False, "error": str(e)[:200]}

    # 3) river stations (5 provinces)
    wl_all = []
    try:
        wl_all = fetch_tw_level(REGION, zref)
        src["thaiwater_level"] = {"ok": True, "stations": len(wl_all)}
    except Exception as e:
        src["thaiwater_level"] = {"ok": False, "error": str(e)[:200]}
    bb = (prm.lon.min() - 0.05, prm.lat.min() - 0.05, prm.lon.max() + 0.05, prm.lat.max() + 0.05)
    wl_st = [s for s in wl_all if bb[0] <= s["lon"] <= bb[2] and bb[1] <= s["lat"] <= bb[3]]
    by_code = {s["code"]: s for s in wl_all}
    rdep, backwater, nearest = model.river_depth(prm, wl_st)
    hist = update_gauge_history(os.path.join(lv, "gauges_hist.json"),
                                [s for s in wl_all if s["code"] in set(upm.UP_GAUGES) | {r[1] for r in upm.REACHES.values()}])

    # 4) upstream inflow (4 provinces)
    ext = None; R = np.zeros((T, prm.n), np.float32); up_out = None
    inj = np.zeros((T, prm.n)); reach_hex = {}
    netj = J("network.json")
    use_v3 = bool(netj) and all(f"z_p{q}" in p for q in model.ZQ)
    if zones and entries:
        up = upm.Upstream(zones, entries, p)
        Qz, genz = up.run(Pz)
        zscale, kreach, kprov = up.calibrate(Qz, i_now, by_code)
        uent, Qe, Qp, gen = up.aggregate(Qz, genz, zscale)
        reach_of = np.array([up.river_of_entry(h) or "" for h in uent])
        minor = reach_of == ""
        ext = np.zeros((T, prm.n))
        np.add.at(ext.T, uent[minor], (Qe[:, minor] * 3.6 / hk).T)      # m3/s -> mm/h over hex
        # reach discharge forecast with gauge assimilation
        rivers = []; qfc = {}
        for key, (name, gcode, _, _) in upm.REACHES.items():
            g = by_code.get(gcode, {})
            qm = Qe[:, reach_of == key].sum(1) if key != "cpy" else np.zeros(T)
            qobs = g.get("q") if g.get("q") is not None else g.get("q_est")
            qfc[key] = (qm, qobs, g)
        # C.2 = obs + Δping + Δnan (lag 12 h)
        dP = qfc["ping"][0] - qfc["ping"][0][i_now]; dN = qfc["nan"][0] - qfc["nan"][0][i_now]
        sh = lambda a, k: np.concatenate([np.full(k, a[0]), a[:-k]])
        qfc["cpy"] = (sh(dP, 12) + sh(dN, 12), qfc["cpy"][1], qfc["cpy"][2])
        reach_hex.update(upm.assign_reaches(p, by_code))
        for key, (name, gcode, _, _) in upm.REACHES.items():
            qm, qobs, g = qfc[key]
            base = qobs if qobs is not None else None
            Qf = np.full(T, np.nan)
            if base is not None:
                Qf[i_now:] = np.maximum(base + (qm[i_now:] - qm[i_now]) if key != "cpy" else base + qm[i_now:], 0.3 * base)
            qbf = g.get("qmax")
            hexes = reach_hex.get(key, np.array([], int))
            fp = {"area_now": 0.0, "area_max": 0.0, "vol_max": 0.0}
            if base is not None and qbf and hexes.size:
                # initial floodplain volume from observed stage (HAND level = WL - z_ref when over bank)
                h0 = 0.0; bh = []
                for c in upm.REACHES[key][2]:
                    s = by_code.get(c)
                    if s and s.get("z_ref") is not None:
                        bh.append(s["bank"] - s["z_ref"])
                        if s["diff"] > 0:
                            h0 = max(h0, s["wl"] - s["z_ref"])
                gs = by_code.get(gcode) or {}
                bh_g = (gs["bank"] - gs["z_ref"]) if gs.get("z_ref") is not None else None
                bank_hand = float(np.clip(bh_g if bh_g and bh_g > 0 else (np.median(bh) if bh else 2.0), 0.5, 10))
                S0 = upm.level_volume(p, hexes, h0) if h0 > 0 else 0.0
                dep, Fh, S, hlev = upm.floodplain(p, hexes, Qf[i_now:], qbf, S0, bank_hand)
                if use_v3:
                    # น้ำส่วนเกินความจุลำน้ำ -> ฉีดเข้า hex ลำน้ำหลักของช่วงนั้น แล้วให้โมเดล fill-spill กระจายตามระดับผิวน้ำ/คันกั้นน้ำ
                    fac = np.asarray(p["facc_km2"])[hexes]
                    ch = hexes[fac >= 1000] if (fac >= 1000).any() else hexes[np.argsort(np.asarray(p["handM_p10"])[hexes])[:5]]
                    wts = np.asarray(p["f_low"])[ch] + 0.1; wts = wts / wts.sum()
                    vol = np.zeros(T); vol[i_now:] = np.maximum(Qf[i_now:] - qbf, 0) * 3600.0; vol[i_now] += S0
                    inj[:, ch] += vol[:, None] * wts[None] / (1000.0 * hk)
                else:
                    R[i_now:, hexes] = np.maximum(R[i_now:, hexes], dep)
                fp = {"area_now": round(float((Fh[0] * (dep[0] >= model.FLOOD_CM)).sum() * hk), 1),
                      "area_max": round(float(((Fh * (dep >= model.FLOOD_CM)).sum(1)).max() * hk), 1),
                      "vol_max": round(float(S.max()) / 1e6, 2), "level_now": round(float(hlev[0]), 2),
                      "bank_hand": round(bank_hand, 2),
                      "hexes": int(hexes.size)}
            t_idx = slice(max(i_now - 72, 0), i_end + 1)
            pk = int(np.nanargmax(Qf[i_now:])) if base is not None else 0
            rivers.append({"key": key, "name": name, "gauge": gcode, "gauge_name": g.get("name", ""),
                           "qmax": qbf, "q_obs": qobs, "q_is_est": g.get("q") is None and qobs is not None, "wl": g.get("wl"), "bank": g.get("bank"), "diff": g.get("diff"),
                           "storage_pct": g.get("storage_pct"), "time": g.get("time"),
                           "t": times[t_idx].tolist(), "q_model": np.round(qm[t_idx], 1).tolist(),
                           "q_fc": [None if np.isnan(v) else round(float(v), 1) for v in Qf[t_idx]],
                           "hist": [[r[0], r[1]] for r in hist.get(gcode, [])],
                           "peak_q": None if base is None else round(float(Qf[i_now + pk]), 1),
                           "peak_t": int(times[i_now + pk]), "floodplain": fp})
        # provinces summary
        prov = []
        zc = np.array(zones["pcode"]); za = np.array(zones["area_km2"])
        names = entries.get("province_names", {})
        for c in sorted(upm.UP_NAMES):
            sel = zc == c; A = za[sel].sum()
            if A == 0:
                continue
            aw = lambda sl: float((Pz[sl][:, sel].sum(0) * za[sel]).sum() / A)
            q = Qp[c]
            prov.append({"code": c, "name": names.get(str(c), upm.UP_NAMES[c]),
                         "area_km2": entries["province_area_km2"].get(str(c)), "contrib_km2": round(float(A)),
                         "rain7d": round(aw(slice(i_now - 167, i_now + 1)), 1), "rain72": round(aw(slice(i_now - 71, i_now + 1)), 1),
                         "fc72": round(aw(slice(i_now + 1, i_end + 1)), 1),
                         "q_now": round(float(q[i_now]), 1), "q_max72": round(float(q[i_now:].max()), 1),
                         "vol_in_7d_mcm": round(float(q[i_now - 167:i_now + 1].sum() * 3600 / 1e6), 1),
                         "vol_in_72h_mcm": round(float(q[i_now + 1:].sum() * 3600 / 1e6), 1),
                         "gen_7d_mcm": round(float(gen[c][i_now - 167:i_now + 1].sum() / 1e6), 1)})
        s6 = list(range(max(i_now - 168, 0), i_end + 1, 3))
        mi = np.where(minor)[0]
        top = sorted([(float(Qe[i_now:, j].max()), j) for j in mi], reverse=True)[:8]
        up_out = {"rivers": rivers, "provinces": prov,
                  "series": {"t": [int(times[i]) for i in s6], "now": int(times[i_now]),
                             "q": {str(c): [round(float(Qp[c][i]), 1) for i in s6] for c in upm.UP_NAMES}},
                  "minor_entries": [{"hex": int(uent[j]), "amphoe": p["amphoe_list"][p["amph"][int(uent[j])]],
                                     "lon": p["lon"][int(uent[j])], "lat": p["lat"][int(uent[j])],
                                     "q_now": round(float(Qe[i_now, j]), 1), "q_max72": round(qx, 1)} for qx, j in top],
                  "calib": {"beta": 0.6, "group": {k_: round(v, 3) for k_, v in kreach.items()},
                            "province": {str(c): round(v, 3) for c, v in kprov.items()}},
                  "total": {"q_now": round(float(sum(Qp[c][i_now] for c in Qp)), 1),
                            "vol_in_72h_mcm": round(float(sum(Qp[c][i_now + 1:].sum() for c in Qp) * 3600 / 1e6), 1),
                            "vol_in_7d_mcm": round(float(sum(Qp[c][i_now - 167:i_now + 1].sum() for c in Qp) * 3600 / 1e6), 1)},
                  "gauges": [{k: s.get(k) for k in ("code", "name", "river", "province", "lat", "lon", "wl", "bank", "diff",
                                                    "q", "qmax", "storage_pct", "situation", "trend", "time")}
                             for s in wl_all if s["pcode"] in UP_PROV and s["code"] in upm.UP_GAUGES]}
        src["upstream"] = {"ok": True, "zones": int(zones["n"]), "entries": int(len(uent))}

    # 5) NS simulation
    pumped72 = None; wse_now = None; frac = None
    if use_v3:
        nw = model.Network(netj, p, J("drainage_assets.json"), J("calibration.json"))
        t2d = max(i_now - HOT_PAST_H, 0)
        sim = lambda a, b, stt, jj: model.simulate_v3(P, prm, nw, backwater, ext, jj, t_start=a, t_end=b, state=stt)
        dA, fA, sA = sim(0, t2d, None, None)
        json.dump({"t0": int(times[t2d]), "wse": np.round(sA["wse"], 3).tolist(),
                   "free": np.round(np.maximum(sA["W"] - prm.normal, 0), 1).tolist()},
                  open(os.path.join(lv, "hex_state.json"), "w"), separators=(",", ":"))
        dB, fB, sB = sim(t2d, i_now, sA, None)
        try:                                                        # อินพุตสำหรับ pipeline/calibrate.py (ไม่ deploy)
            import tempfile
            np.savez_compressed(os.path.join(tempfile.gettempdir(), "nsflood_sim_inputs.npz"), P=P.astype(np.float32),
                                ext=(ext if ext is not None else np.zeros_like(P)).astype(np.float32),
                                inj=inj.astype(np.float32), bw=backwater, i_now=i_now, times=times)
        except Exception:  # noqa
            pass
        dW0, fW0, sW0 = sim(i_now, i_now + 1, sB, inj); dW1, fW1, sW1 = sim(i_now + 1, T, sW0, inj)
        dN0, fN0, sN0 = sim(i_now, i_now + 1, sB, None); dN1, fN1, _ = sim(i_now + 1, T, sN0, None)
        depth = np.vstack([dA, dB, dW0, dW1]); frac = np.vstack([fA, fB, fW0, fW1])
        depth_rain = np.vstack([dA, dB, dN0, dN1])
        R = np.maximum(depth - depth_rain, 0)
        wse_now = sW0["wse"]
        pumped72 = float(np.concatenate([sW0["pumped"], sW1["pumped"]])[:73].sum()) * 1000 * hk / 1e6   # ล้าน ลบ.ม.
        river_now = R[i_now] >= model.FLOOD_CM
        cls = model.depth_class(depth)
        hrs, _ = model.durations(depth_rain, i_now)
        _, rem = model.durations(depth, i_now)
        hrs = np.where(river_now & (depth_rain[i_now] < model.FLOOD_CM), -1, hrs)
        # รายงานพื้นที่น้ำล้นตลิ่งราย reach จากโมเดลรวม
        if up_out:
            for r in up_out["rivers"]:
                hx = reach_hex.get(r["key"], np.array([], int))
                if hx.size:
                    a_t = (frac[i_now:, hx] * (R[i_now:, hx] >= model.FLOOD_CM)).sum(1) * hk
                    r["floodplain"].update({"area_now": round(float(a_t[0]), 1), "area_max": round(float(a_t.max()), 1)})
    else:
        depth_rain = model.simulate(P, prm, backwater, ext)
        R[i_now] = np.maximum(R[i_now], rdep)
        depth = np.maximum(depth_rain, R)
        cls = model.depth_class(depth)
        hrs, _ = model.durations(depth_rain, i_now)
        _, rem = model.durations(depth, i_now)
        river_now = R[i_now] >= model.FLOOD_CM
        hrs = np.where(river_now & (depth_rain[i_now] < model.FLOOD_CM), -1, hrs)
        for k, s in enumerate(wl_st):                              # stage-only reaches: use trend
            sel = (nearest == k) & (rdep >= model.FLOOD_CM) & (rem < 0)
            if sel.any():
                tr = s.get("trend") or 0
                rem = np.where(sel, 999 if tr >= 0 else min(int(s["diff"] / -tr) + 1, 999), rem)
    if frac is None:
        frac = (depth > 0).astype(np.float32)                      # v2: ทั้ง hex
    srcflag = (depth_rain[i_now] >= model.FLOOD_CM).astype(int) + 2 * river_now.astype(int)
    area = lambda t_, m_: float((frac[t_] * m_).sum() * hk)       # พื้นที่ท่วมจริง (ถ่วงสัดส่วนพื้นที่ใน hex)

    fut = slice(i_now + 1, i_end + 1)
    def win(h): return slice(i_now + 1, min(i_now + 1 + h, i_end + 1))
    status = {
        "n": prm.n, "d": np.round(depth[i_now]).astype(int).tolist(), "c": cls[i_now].astype(int).tolist(),
        "s": srcflag.tolist(), "h": hrs.tolist(), "r": rem.tolist(),
        "p24": np.round(P[i_now - 23:i_now + 1].sum(0)).astype(int).tolist(),
        "p72": np.round(P[i_now - 71:i_now + 1].sum(0)).astype(int).tolist(),
        "p7d": np.round(P[i_now - 167:i_now + 1].sum(0)).astype(int).tolist(),
        "f24": np.round(P[win(24)].sum(0)).astype(int).tolist(), "f72": np.round(P[fut].sum(0)).astype(int).tolist(),
        "m24": cls[win(24)].max(0).astype(int).tolist(), "m72": cls[fut].max(0).astype(int).tolist(),
        "dm72": np.round(depth[fut].max(0)).astype(int).tolist(),
        "u72": np.round(R[fut].max(0)).astype(int).tolist(),            # ความลึกจากน้ำล้นตลิ่ง/ต้นน้ำ สูงสุด 72 ชม.
        "ui": np.round(ext[i_now - 71:i_now + 1].sum(0) if ext is not None else np.zeros(prm.n)).astype(int).tolist(),
        "f": np.round(frac[i_now] * 100).astype(int).tolist(),
    }
    if wse_now is not None:
        status["wse"] = np.round(wse_now, 2).tolist()
    fr_idx = list(range(max(i_now - FRAME_PAST_H, 0), i_end + 1, FRAME_STEP))
    if i_now not in fr_idx:
        fr_idx = sorted(set(fr_idx + [i_now]))
    frames = {"t": [int(times[i]) for i in fr_idx], "now": int(times[i_now]),
              "c": ["".join(map(str, cls[i].tolist())) for i in fr_idx]}

    amph = np.asarray(p["amph"]); names = p["amphoe_list"]; dist = []
    m72 = np.asarray(status["m72"])
    for a, nm in enumerate(names):
        sel = amph == a
        if not sel.any():
            continue
        c_now = cls[i_now][sel]; fs_ = frac[i_now][sel]
        f72 = (frac[fut][:, sel] * (cls[fut][:, sel] >= 2)).sum(1).max() * hk if i_end > i_now else 0
        dist.append({"amphoe": nm, "hex": int(sel.sum()),
                     "km2_now": round(float((fs_ * (c_now >= 2)).sum() * hk), 1), "km2_watch": round(float((fs_ * (c_now >= 1)).sum() * hk), 1),
                     "km2_72h": round(float(f72), 1),
                     "km2_river72": round(float(((frac[fut][:, sel] * (R[fut][:, sel] >= model.FLOOD_CM)).sum(1).max() if i_end > i_now else 0) * hk), 1),
                     "max_cls_now": int(c_now.max()), "max_cls_72h": int(m72[sel].max()),
                     "rain24": round(float(P[i_now - 23:i_now + 1, sel].sum(0).mean()), 1),
                     "rain72": round(float(P[i_now - 71:i_now + 1, sel].sum(0).mean()), 1),
                     "fc72": round(float(P[fut][:, sel].sum(0).mean()), 1)})
    dist.sort(key=lambda d: (-d["max_cls_now"], -d["km2_now"], -d["km2_72h"]))
    series = {"t": [int(x) for x in times[max(i_now - FRAME_PAST_H, 0):]],
              "mean": np.round(P[max(i_now - FRAME_PAST_H, 0):].mean(1), 2).tolist(),
              "max": np.round(P[max(i_now - FRAME_PAST_H, 0):].max(1), 2).tolist(), "now": int(times[i_now])}

    key = os.environ.get("GISTDA_API_KEY", "").strip()
    if key:
        try:
            src["gistda"] = {"ok": True, "features": fetch_gistda(key, os.path.join(lv, "gistda_flood.geojson"))}
        except Exception as e:
            src["gistda"] = {"ok": False, "error": str(e)[:200]}
    else:
        src["gistda"] = {"ok": False, "error": "ไม่ได้ตั้ง GISTDA_API_KEY"}

    gen_t = datetime.now(TZ)
    meta = {"generated": gen_t.isoformat(timespec="seconds"), "now": int(times[i_now]),
            "model": ("hex-network v3 (SCS-CN + multi-direction drainage + fill-spill + drainage assets + upstream inflow)"
                      if use_v3 else "ponding-cascade v2 (SCS-CN + hex routing + upstream inflow + HAND floodplain)"), "adjust": adj_note,
            "calibration": J("calibration.json"),
            "sources": src, "runtime_s": round(time.time() - t0, 1),
            "classes": {"cm": list(model.CLASS_CM), "labels": ["ไม่ท่วม", "เฝ้าระวัง", "ท่วมขังเล็กน้อย", "ท่วมขังปานกลาง", "ท่วมสูง"]},
            "water_hex": int(prm.is_water.sum()),
            "summary": {"km2_now": round(area(i_now, cls[i_now] >= 2), 1),
                        "km2_watch": round(area(i_now, cls[i_now] >= 1), 1),
                        "km2_72h": round(max(area(t_, cls[t_] >= 2) for t_ in range(i_now, i_end + 1)), 1),
                        "pumped_72h_mcm": None if pumped72 is None else round(pumped72, 2),
                        "km2_river72": round(sum((r["floodplain"] or {}).get("area_max", 0) for r in (up_out or {}).get("rivers", [])), 1),
                        "rain24_max": int(max(status["p24"])), "fc72_max": int(max(status["f72"])),
                        "wl_over_bank": sum(1 for s in wl_st if s["diff"] > 0 and s.get("province") == "นครสวรรค์"),
                        "up_vol72_mcm": (up_out or {}).get("total", {}).get("vol_in_72h_mcm")}}
    W = lambda n, o: json.dump(o, open(os.path.join(lv, n), "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))
    W("status.json", status); W("frames.json", frames); W("districts.json", dist); W("series.json", series)
    W("stations.json", {"rain": [s for s in rain_st if s["pcode"] == "60"], "level": wl_st})
    if up_out:
        W("upstream.json", up_out)
    W("meta.json", meta)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=".")
    main(ap.parse_args().site)
