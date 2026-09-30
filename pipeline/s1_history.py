# -*- coding: utf-8 -*-
"""
น้ำท่วมในอดีตจาก Sentinel-1 (2560–2568) สำหรับแอ่งท่าตะโก — ใช้ตรวจว่า "ท่วมซ้ำซาก/นาน" อยู่ตรงไหนจริง และเทียบกับแบบจำลอง 2D

  1) download(out_dir)  : ภาพ RTC gamma0 VV (Planetary Computer, relative orbit 62 ขาลง — ภาพเดียวครอบทั้งพื้นที่)
                          ฤดูน้ำหลาก 15 ก.ค.–31 ธ.ค. + ฤดูแล้ง ก.พ.–เม.ย. ของทุกปี ; ตัดภาพที่ server (data API /item/bbox)
                          กริด 1500×1500 บน WGS84 (~36 ม.) resampling=average -> <date>_<wet|dry>.npz (dB×10, int16)
                          ต้องมีแค่ urllib + GDAL (ArcGIS Pro มีครบ ; GDAL ของ Pro อ่าน /vsicurl ไม่ได้ จึงให้ server ตัดภาพ)
  2) analyze(out_dir, site) : จำแนกน้ำ -> ระยะเวลาท่วมรายปีราย pixel -> สรุปรายตำบล + ความถี่ + เทียบกับผล 2D
จำแนกน้ำ: VV_dB < T (จุดต่ำสุดของ histogram ระหว่างโหมดน้ำ–บก ในช่วง −17…−13 dB) และ VV_dB − VV_ref_dB < −3 dB (ref = มัธยฐานฤดูแล้งปีเดียวกัน)
  -> ตัดแหล่งน้ำถาวรและพื้นผิวที่มืดอยู่แล้ว ; majority 3×3 ; นับเฉพาะ 1 ส.ค.–15 ธ.ค. (ลดการสับสนกับนาที่ขังน้ำตอนเตรียมแปลง)
ระยะเวลาท่วม (วัน) = Σ ภาพที่เป็นน้ำ × (t_ถัดไป − t_ก่อนหน้า)/2 ; ปีที่ "ท่วมนาน" = ≥ 30 วัน
ข้อจำกัด: น้ำใต้ต้นไม้/พืชสูงตรวจไม่พบ, ช่วงห่างภาพ 6 วัน (2560–2564) / 12 วัน (2565–) , เมือง/หลังคาเกิด double bounce
"""
import json, os, time, urllib.request
from datetime import date
import numpy as np

BBOX = (100.15, 15.38, 100.66, 15.88); W = H = 1500
DX = (BBOX[2] - BBOX[0]) / W; DY = (BBOX[3] - BBOX[1]) / H


def pixel_lonlat():
    lon = BBOX[0] + (np.arange(W) + 0.5) * DX; lat = BBOX[3] - (np.arange(H) + 0.5) * DY
    return np.meshgrid(lon, lat)


def pixel_km2():
    lat = BBOX[3] - (np.arange(H) + 0.5) * DY
    return (DX * 111.32 * np.cos(np.radians(lat)))[:, None] * (DY * 110.57) * np.ones((1, W))


def _valley(v, lo=-17.0, hi=-13.0):
    """เกณฑ์น้ำ = จุดต่ำสุดของ histogram (ปรับเรียบ) ระหว่างโหมดน้ำกับโหมดบก ในช่วง lo…hi dB
    (Otsu ถูกดึงไปทางบกเพราะพิกเซลบกมากกว่ามาก — ทดสอบ 17 ต.ค. 2560: Otsu −11.8 dB, หุบจริง ~−14.2 dB)"""
    h, e = np.histogram(v, bins=160, range=(-30, 10)); c = (e[:-1] + e[1:]) / 2
    hs = np.convolve(h, np.ones(5) / 5, mode="same")
    k = (c >= lo) & (c <= hi)
    return float(c[k][np.argmin(hs[k])])


def _rasterize(features):
    """even-odd point-in-polygon ของจุดกลาง pixel -> index ตำบล (-1 = นอก)"""
    lon, lat = pixel_lonlat(); out = np.full((H, W), -1, np.int16)
    for k, f in enumerate(features):
        g = f["geometry"]; polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        inside = np.zeros((H, W), bool)
        for poly in polys:
            for ring in poly:
                r = np.asarray(ring); x0, x1 = r[:, 0].min(), r[:, 0].max(); y0, y1 = r[:, 1].min(), r[:, 1].max()
                cs = slice(max(int((x0 - BBOX[0]) / DX) - 1, 0), min(int((x1 - BBOX[0]) / DX) + 2, W))
                rs = slice(max(int((BBOX[3] - y1) / DY) - 1, 0), min(int((BBOX[3] - y0) / DY) + 2, H))
                X = lon[rs, cs]; Y = lat[rs, cs]; c = np.zeros(X.shape, bool)
                xa, ya = r[:-1, 0], r[:-1, 1]; xb, yb = r[1:, 0], r[1:, 1]
                for i in range(len(xa)):
                    cond = (ya[i] > Y) != (yb[i] > Y)
                    xi = xa[i] + (Y - ya[i]) * (xb[i] - xa[i]) / np.where(yb[i] == ya[i], 1e-12, yb[i] - ya[i])
                    c ^= cond & (X < xi)
                inside[rs, cs] ^= c
        out[inside] = k
    return out


def download(out_dir, years=range(2017, 2026), orbit=62, log=print):
    os.makedirs(out_dir, exist_ok=True)
    from osgeo import gdal
    gdal.UseExceptions()

    def search(dt):
        body = json.dumps({"collections": ["sentinel-1-rtc"], "bbox": list(BBOX), "datetime": dt, "limit": 200,
                           "query": {"sat:relative_orbit": {"eq": orbit}}}).encode()
        req = urllib.request.Request("https://planetarycomputer.microsoft.com/api/stac/v1/search", data=body,
                                     headers={"Content-Type": "application/json"})
        fs = json.load(urllib.request.urlopen(req, timeout=120))["features"]
        return [f for f in fs if f["bbox"][0] <= BBOX[0] and f["bbox"][1] <= BBOX[1] and f["bbox"][2] >= BBOX[2] and f["bbox"][3] >= BBOX[3]]

    for y in years:
        for tag, dt in (("wet", f"{y}-07-15/{y}-12-31"), ("dry", f"{y}-02-01/{y}-04-30")):
            for f in search(dt):
                fn = os.path.join(out_dir, f"{f['properties']['datetime'][:10]}_{tag}.npz")
                if os.path.exists(fn):
                    continue
                u = (f"https://planetarycomputer.microsoft.com/api/data/v1/item/bbox/{BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]}/{W}x{H}.tif"
                     f"?collection=sentinel-1-rtc&item={f['id']}&assets=vv&nodata=0&resampling=average")
                p = os.path.join(out_dir, "_tmp.tif")
                open(p, "wb").write(urllib.request.urlopen(u, timeout=300).read())
                ds = gdal.Open(p); a = ds.GetRasterBand(1).ReadAsArray().astype("float32"); m = ds.GetRasterBand(2).ReadAsArray() > 0; ds = None
                ok = (a > 0) & m
                db = np.where(ok, np.round(10 * np.log10(np.where(ok, a, 1)) * 10), -32768).astype(np.int16)
                np.savez_compressed(fn, db=db, iid=f["id"]); log(fn)


def _load(fn):
    d = np.load(fn)["db"].astype(np.float32); v = d > -32000
    return np.where(v, d / 10.0, np.nan)


def analyze(out_dir, site, season=("08-01", "12-15"), min_days=30, log=print):
    from scipy import ndimage
    fs = sorted(f for f in os.listdir(out_dir) if f.endswith(".npz") and f[:4].isdigit())
    years = sorted({int(f[:4]) for f in fs})
    ca = pixel_km2()
    aoi = json.load(open(os.path.join(site, "data", "static", "hotspots", "thatako_aoi.geojson"), encoding="utf8"))["features"]
    zone = _rasterize(aoi)
    res = {"bbox": BBOX, "size": [H, W], "years": {}, "tambon": [f["properties"]["name"] for f in aoi],
           "district": [f["properties"]["district"] for f in aoi], "in_existing": [f["properties"].get("in_existing", False) for f in aoi],
           "method": "VV<หุบ histogram(−17…−13 dB) & VV−ref_dry<−3 dB, majority 3×3, ส.ค.–15 ธ.ค., relative orbit 62"}
    dur_all = np.zeros((len(years), H, W), np.float32); perm_any = np.zeros((H, W), bool)
    for yi, y in enumerate(years):
        dry = [f for f in fs if f.startswith(str(y)) and f.endswith("_dry.npz")]
        wet = [f for f in fs if f.startswith(str(y)) and f.endswith("_wet.npz") and f"{y}-{season[0]}" <= f[:10] <= f"{y}-{season[1]}"]
        if not wet:
            continue
        if not dry:                                                    # ไม่มีภาพแล้งปีนั้น -> ใช้ปีใกล้สุด
            cand = sorted((f for f in fs if f.endswith("_dry.npz")), key=lambda f: abs(int(f[:4]) - y))
            dry = [f for f in cand if f[:4] == cand[0][:4]] if cand else []
        ref = np.nanmedian(np.stack([_load(os.path.join(out_dir, f)) for f in dry]), 0) if dry else None
        perm = (ref < -15) if ref is not None else np.zeros((H, W), bool)
        perm_any |= perm
        t = np.array([date.fromisoformat(f[:10]).toordinal() for f in wet], float)
        wt = np.empty_like(t)                                          # น้ำหนักวันของแต่ละภาพ
        tt = np.concatenate([[t[0] - (t[1] - t[0] if len(t) > 1 else 12)], t, [t[-1] + (t[-1] - t[-2] if len(t) > 1 else 12)]])
        wt[:] = (tt[2:] - tt[:-2]) / 2
        dur = np.zeros((H, W), np.float32); area_ts = []; zone_ts = []
        for f, w in zip(wet, wt):
            db = _load(os.path.join(out_dir, f)); v = ~np.isnan(db)
            T = _valley(db[v])
            wat = v & (db < T)
            if ref is not None:
                wat &= (db - np.nan_to_num(ref, nan=0) < -3) | np.isnan(ref)
            wat = ndimage.uniform_filter(wat.astype(np.float32), 3) > 0.5
            wat &= ~perm
            dur += wat * w
            area_ts.append([f[:10], round(float((wat * ca).sum()), 1), round(T, 1)])
            zone_ts.append(np.bincount(zone[wat & (zone >= 0)], weights=ca[wat & (zone >= 0)], minlength=len(aoi)).round(2).tolist())
        dur_all[yi] = dur
        z = zone >= 0
        per = []
        for k in range(len(aoi)):
            m = zone == k
            per.append({"flood_km2_max": round(max(zt[k] for zt in zone_ts), 2),
                        "ge30d_km2": round(float(ca[m & (dur >= 30)].sum()), 2), "ge60d_km2": round(float(ca[m & (dur >= 60)].sum()), 2),
                        "ge90d_km2": round(float(ca[m & (dur >= 90)].sum()), 2),
                        "dur_p90_d": round(float(np.percentile(dur[m & (dur > 0)], 90)), 0) if (m & (dur > 0)).any() else 0})
        res["years"][str(y)] = {"n_img": len(wet), "dates": [a[0] for a in area_ts], "area_bbox_km2": [a[1] for a in area_ts],
                                "thr_db": [a[2] for a in area_ts], "n_dry": len(dry), "tambon": per,
                                "tambon_ts": zone_ts,
                                "aoi_ge30d_km2": round(float(ca[z & (dur >= 30)].sum()), 1), "aoi_ge60d_km2": round(float(ca[z & (dur >= 60)].sum()), 1)}
        log(y, len(wet), res["years"][str(y)]["aoi_ge30d_km2"], res["years"][str(y)]["aoi_ge60d_km2"])
    freq30 = (dur_all >= min_days).sum(0); freq60 = (dur_all >= 60).sum(0)
    res["n_years"] = len(years); res["perm_water_km2"] = round(float(ca[perm_any & (zone >= 0)].sum()), 1)
    res["freq"] = []
    for k in range(len(aoi)):
        m = zone == k
        res["freq"].append({"ge30d_in_ge3y_km2": round(float(ca[m & (freq30 >= 3)].sum()), 2),
                            "ge30d_in_ge5y_km2": round(float(ca[m & (freq30 >= 5)].sum()), 2),
                            "ge60d_in_ge3y_km2": round(float(ca[m & (freq60 >= 3)].sum()), 2),
                            "mean_years_ge30d": round(float(freq30[m].mean()), 2)})
    np.savez_compressed(os.path.join(out_dir, "_summary_arrays.npz"), dur=dur_all.astype(np.float16), years=np.array(years),
                        freq30=freq30.astype(np.uint8), freq60=freq60.astype(np.uint8), zone=zone, perm=perm_any)
    json.dump(res, open(os.path.join(out_dir, "s1_history.json"), "w", encoding="utf8"), ensure_ascii=False)
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--site", default=".")
    ap.add_argument("--download", action="store_true"); a = ap.parse_args()
    if a.download:
        download(a.out)
    analyze(a.out, a.site)
