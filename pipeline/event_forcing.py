# -*- coding: utf-8 -*-
"""
ข้อมูลนำเข้าแบบจำลองเหตุการณ์ย้อนหลัง (event_2d.py) — ระดับน้ำรายชั่วโมงของสถานี + ฝน ERA5 รายชั่วโมงบน lattice 0.1°

  python pipeline/event_forcing.py --site . --hid prov_ev --year 2021 [--start 08-01 --end 12-16]

  stations: ThaiWater waterlevel_graph (station_type=tele_waterlevel) ทีละ ~10 วัน (API ล้มบ่อยเมื่อขอช่วงยาว) -> [[เวลาท้องถิ่น, WL ม.รทก., Q], ...]
            + min_bank / ground / qmax / พิกัด ของสถานีจาก public/waterlevel
  rain:     Open-Meteo archive (ERA5, best match) hourly precipitation บน lattice 0.1° ครอบกรอบโดเมน ; ทีละ 40 จุด
ต้องต่อเน็ตได้ทั้ง api-v3.thaiwater.net และ archive-api.open-meteo.com (รันใน Python ของ ArcGIS Pro บนเครื่องผู้ใช้ได้)
"""
import argparse, json, os, time, urllib.request
from datetime import date, timedelta
import numpy as np

TW = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/"
OM = "https://archive-api.open-meteo.com/v1/archive"
STATIONS = {"prov_ev": ["P.16", "P.17", "Y.5", "N.67", "C.2", "CPY002", "C.13", "Ct.5A", "Ct.4", "Ct.19", "Ct.2A"],
            "thatako_ev": ["Y.5", "N.67", "NAN008", "C.2", "CPY001", "YOM009"]}


def get(url, tries=6, timeout=90):
    err = None
    for k in range(tries):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "nsf-flood/1.0"}), timeout=timeout))
            if isinstance(r, dict) and r.get("result") == "NO":
                raise RuntimeError(str(r.get("data"))[:120])
            return r
        except Exception as e:  # noqa
            err = e; time.sleep(5 * (k + 1))
    raise RuntimeError(f"{url[:90]}: {err}")


def stations_meta(codes):
    L = get(TW + "waterlevel")["data"]
    out = {}
    for x in L:
        s = x["station"]; c = s.get("tele_station_oldcode")
        if c in codes and c not in out:
            out[c] = {"sid": s["id"], "name": s["tele_station_name"]["th"], "lat": s["tele_station_lat"], "lon": s["tele_station_long"],
                      "min_bank": s.get("min_bank"), "ground": s.get("ground_level"), "qmax": s.get("qmax"), "river": x.get("river_name")}
    return out


def station_series(sid, d0, d1, step=10, log=print):
    rows = {}
    d = d0
    while d <= d1:
        e = min(d + timedelta(days=step - 1), d1)
        try:
            g = get(TW + f"waterlevel_graph?station_type=tele_waterlevel&station_id={sid}&start_date={d}&end_date={e}")["data"]["graph_data"]
            for x in g:
                if x.get("value") is not None:
                    rows[x["datetime"][:16]] = [x["datetime"][:16], round(float(x["value"]), 3),
                                                None if x.get("discharge") is None else float(x["discharge"])]
        except Exception as ex:  # noqa
            log(f"   ! {sid} {d}..{e}: {ex}")
        d = e + timedelta(days=1)
    return [rows[k] for k in sorted(rows)]


def rain_lattice(bbox, d0, d1, step=0.1, chunk=40, log=print):
    w, s, e, n = bbox
    lats = np.round(np.arange(s + step / 2, n, step), 3); lons = np.round(np.arange(w + step / 2, e, step), 3)
    LA, LO = [a.ravel() for a in np.meshgrid(lats, lons, indexing="ij")]
    P = []
    for a in range(0, LA.size, chunk):
        url = (f"{OM}?latitude={','.join(map(str, LA[a:a + chunk]))}&longitude={','.join(map(str, LO[a:a + chunk]))}"
               f"&start_date={d0}&end_date={d1}&hourly=precipitation&timezone=UTC")
        r = get(url, timeout=180)
        r = r if isinstance(r, list) else [r]
        P += [x["hourly"]["precipitation"] for x in r]
        log(f"   rain {a + len(r)}/{LA.size}")
        time.sleep(2)
    return {"lat": LA.tolist(), "lon": LO.tolist(), "t0_utc": f"{d0}T00:00", "P": P}


def main(site, hid, year, start="08-01", end="12-16", what=("stations", "rain"), log=print):
    meta = json.loads(str(np.load(os.path.join(site, "data", "static", "hotspots", f"{hid}.npz"))["meta"]))
    (s, w), (n, e) = meta["bounds"]
    d0 = date.fromisoformat(f"{year}-{start}"); d1 = date.fromisoformat(f"{year}-{end}")
    od = os.path.join(site, "hecras", hid, "forcing"); os.makedirs(od, exist_ok=True)
    fn = os.path.join(od, f"{year}.json")
    F = json.load(open(fn)) if os.path.exists(fn) else {"stations": {}, "station_meta": {}}
    sm = stations_meta(STATIONS[hid]) if "stations" in what else {}
    if sm:
        F["station_meta"] = sm
    for c, m in sm.items():
        if len(F["stations"].get(c, [])) > 24 * 100:
            continue
        F["stations"][c] = station_series(m["sid"], d0, d1, log=log)
        log(f"  {c}: {len(F['stations'][c])} ชม.")
        json.dump(F, open(fn, "w"))
    if "rain" in what and "rain" not in F:
        F["rain"] = rain_lattice((w, s, e, n), d0, d1, log=log)
        json.dump(F, open(fn, "w"))
    log(f"{year} -> {fn}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="."); ap.add_argument("--hid", default="prov_ev"); ap.add_argument("--year", type=int, nargs="+", required=True)
    ap.add_argument("--start", default="08-01"); ap.add_argument("--end", default="12-16")
    ap.add_argument("--only", choices=["stations", "rain"], default=None)
    a = ap.parse_args()
    for y in a.year:
        main(a.site, a.hid, y, a.start, a.end, what=(a.only,) if a.only else ("stations", "rain"))
