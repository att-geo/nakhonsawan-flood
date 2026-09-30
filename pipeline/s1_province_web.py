# -*- coding: utf-8 -*-
"""
สรุปผล s1_province.py ขึ้นเว็บ / รายงาน
  -> data/static/s1_province.json   (สรุปรายตำบล รายอำเภอ รายปี — ใช้บนเว็บ)
  -> data/static/s1_province_freq.png  (จำนวนปีที่ท่วม ≥ 30 วัน, Leaflet imageOverlay บน bounds)
  -> s1_province_tambon.csv            (ตารางรายตำบล เปิดใน Excel/ArcGIS Pro)
  python s1_province_web.py --out ../s1_prov --site .
"""
import csv, json, os
import numpy as np


# 3 ชั้นภาษาง่าย (สีโทนร้อน เพื่อไม่ให้สับสนกับสีฟ้า/น้ำเงินของความลึกน้ำตอนนี้บนเว็บ)
FREQ_CLS = [(1, 2, (253, 211, 77, 170), "บางปี (1–2 ปี)"), (3, 5, (249, 115, 22, 200), "บ่อย (3–5 ปี)"),
            (6, 99, (153, 27, 27, 225), "เกือบทุกปี (6–9 ปี)")]


def colorize(freq, perm, n_years):
    """จำนวนปีที่ท่วม ≥ 30 วัน -> RGBA 3 ชั้น (0 = โปร่งใส ; แหล่งน้ำถาวร = เทาอ่อน)"""
    rgba = np.zeros(freq.shape + (4,), np.uint8)
    for lo, hi, col, _ in FREQ_CLS:
        rgba[(freq >= lo) & (freq <= hi)] = col
    rgba[perm & (freq == 0)] = [148, 163, 184, 120]
    return rgba


def hex_summary(A, site, years, min_frac=0.1):
    """ราย hex 1 กม² ของโมเดล (hex.geojson, index i) : จำนวนปีที่ ≥ 10% ของ hex ท่วม ≥ 30 วัน, ปีล่าสุดที่เป็นเช่นนั้น,
    % พื้นที่ hex ที่ท่วม ≥ 30 วัน ≥ 3 ปี, ตำบลส่วนใหญ่ -> ใช้ใน popup/“บ้านของฉัน” บนเว็บ"""
    from rasterio import features as rf
    from rasterio.transform import from_origin
    from s1_province import BBOX, DX, DY, H, W
    hx = json.load(open(os.path.join(site, "data", "static", "hex.geojson"), encoding="utf8"))["features"]
    n = len(hx)
    grid = rf.rasterize(((f["geometry"], f["properties"]["i"]) for f in hx), out_shape=(H, W),
                        transform=from_origin(BBOX[0], BBOX[3], DX, DY), fill=-1, dtype="int32")
    m = grid >= 0; g = grid[m]
    npx = np.bincount(g, minlength=n).astype(np.float64); npx[npx == 0] = np.nan
    yrs = np.zeros(n, np.int16); last = np.zeros(n, np.int16)
    for k, y in enumerate(years):
        fr = np.bincount(g, weights=(A["dur"][k][m] >= 30), minlength=n) / npx
        hit = np.nan_to_num(fr) >= min_frac
        yrs += hit; last[hit] = int(y)
    rep = np.nan_to_num(np.bincount(g, weights=(A["freq30"][m] >= 3), minlength=n) / npx)
    z = A["zone"][m]; tam = np.full(n, -1, np.int16)
    # ตำบลส่วนใหญ่ของ hex (bincount 2 มิติแบบประหยัด)
    key = (g.astype(np.int64) * 256 + z)[z >= 0]
    u, c = np.unique(key, return_counts=True)
    order = np.lexsort((-c, u // 256)); u = u[order]
    first = np.r_[True, (u[1:] // 256) != (u[:-1] // 256)]
    for kk in u[first]:
        t = int(kk % 256); tam[int(kk // 256)] = -1 if t == 255 else t
    # hex นอกขอบเขตตำบล OCHA (ขอบจังหวัด geoBoundaries ต่างกันเล็กน้อย) -> ตำบลที่ใกล้ที่สุด
    miss = np.where(tam < 0)[0]
    if len(miss):
        from shapely.geometry import shape
        from shapely.strtree import STRtree
        tg = [shape(f["geometry"]) for f in json.load(open(os.path.join(site, "data", "static", "province_tambon.geojson"), encoding="utf8"))["features"]]
        tree = STRtree(tg)
        for i in miss:
            tam[i] = int(tree.nearest(shape(hx[i]["geometry"]).centroid))
    return {"n": n, "yrs": yrs.tolist(), "last": last.tolist(), "rep_pct": np.round(rep * 100).astype(int).tolist(), "tam": tam.tolist(),
            "min_frac": min_frac, "note": "yrs = จำนวนปี (จาก n_years) ที่ ≥ 10% ของ hex ท่วมต่อเนื่อง ≥ 30 วัน ; rep_pct = % พื้นที่ hex ที่ท่วม ≥ 30 วัน อย่างน้อย 3 ปี"}


def main(out_dir, site):
    from PIL import Image
    r = json.load(open(os.path.join(out_dir, "s1_province.json"), encoding="utf8"))
    A = np.load(os.path.join(out_dir, "s1_province_arrays.npz"))
    years = [str(y) for y in r["year_list"]]; bb = r["bbox"]
    freq = A["freq30"]; perm = A["perm"]; zone = A["zone"]
    rgba = colorize(freq, perm, len(years)); rgba[zone < 0] = 0
    img = Image.fromarray(rgba, "RGBA").resize((rgba.shape[1] // 2, rgba.shape[0] // 2), Image.NEAREST)
    st = os.path.join(site, "data", "static"); os.makedirs(st, exist_ok=True)
    img.save(os.path.join(st, "s1_province_freq.png"), optimize=True)
    if os.path.exists(os.path.join(st, "hex.geojson")):
        hs = hex_summary(A, site, years); hs["n_years"] = len(years); hs["years"] = [int(y) for y in years]
        json.dump(hs, open(os.path.join(st, "s1_province_hex.json"), "w"), separators=(",", ":"))
    # ขอบเขตตำบลแบบย่อสำหรับเว็บ
    from shapely.geometry import shape, mapping
    tg = json.load(open(os.path.join(st, "province_tambon.geojson"), encoding="utf8"))["features"]
    rnd = lambda c: [rnd(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], 4), round(c[1], 4)]
    web_t = []
    for k, f in enumerate(tg):
        gm = mapping(shape(f["geometry"]).simplify(0.0006, preserve_topology=True))
        web_t.append({"type": "Feature", "properties": {"k": k, "name": f["properties"]["name"], "district": f["properties"]["district"]},
                      "geometry": {"type": gm["type"], "coordinates": rnd(json.loads(json.dumps(gm["coordinates"])))}})
    json.dump({"type": "FeatureCollection", "features": web_t}, open(os.path.join(st, "tambon_web.geojson"), "w", encoding="utf8"),
              ensure_ascii=False, separators=(",", ":"))

    tam = []
    for k, t in enumerate(r["tambon"]):
        fr = r["freq"][k]; row = dict(t)
        row.update({"ge30d_ge3y_km2": fr["ge30d_in_ge3y_km2"], "ge30d_ge5y_km2": fr["ge30d_in_ge5y_km2"],
                    "ge60d_ge3y_km2": fr["ge60d_in_ge3y_km2"], "mean_years_ge30d": fr["mean_years_ge30d"], "perm_km2": fr["perm_km2"],
                    "ge30d_km2_by_year": {y: r["years"][y]["tambon"][k]["ge30d_km2"] for y in years},
                    "ge60d_km2_by_year": {y: r["years"][y]["tambon"][k]["ge60d_km2"] for y in years},
                    "max_km2_by_year": {y: r["years"][y]["tambon"][k]["flood_km2_max"] for y in years},
                    "dur_p90_d_by_year": {y: r["years"][y]["tambon"][k]["dur_p90_d"] for y in years}})
        tam.append(row)
    dist = {}
    for t in tam:
        d = dist.setdefault(t["district"], {"district": t["district"], "n_tambon": 0, "area_km2": 0, "ge30d_ge3y_km2": 0,
                                            "ge60d_ge3y_km2": 0, "ge60d_km2_by_year": {y: 0 for y in years}})
        d["n_tambon"] += 1; d["area_km2"] += t["area_km2"]; d["ge30d_ge3y_km2"] += t["ge30d_ge3y_km2"]; d["ge60d_ge3y_km2"] += t["ge60d_ge3y_km2"]
        for y in years:
            d["ge60d_km2_by_year"][y] += t["ge60d_km2_by_year"][y]
    for d in dist.values():
        for k in ("area_km2", "ge30d_ge3y_km2", "ge60d_ge3y_km2"):
            d[k] = round(d[k], 1)
        d["ge60d_km2_by_year"] = {y: round(v, 1) for y, v in d["ge60d_km2_by_year"].items()}
    yrs = {}
    for y in years:
        Y = r["years"][y]; a = Y["area_prov_km2"]; i = int(np.argmax(a))
        yrs[y] = {"n_img": Y["n_img"], "n_img_by_orbit": {str(o): Y["orbit"].count(o) for o in r["orbits"]},
                  "prov_ge30d_km2": Y["prov_ge30d_km2"], "prov_ge60d_km2": Y["prov_ge60d_km2"], "peak": Y["dates"][i],
                  "peak_km2": a[i], "ts": [[d, o, v] for d, o, v in zip(Y["dates"], Y["orbit"], a)]}
    ca_note = "พื้นที่ใช้พิกเซล ~36 ม. (WGS84) ; นับเฉพาะในเขตจังหวัด ; ไม่รวมแหล่งน้ำถาวร"
    web = {"source": "Sentinel-1 RTC VV (Microsoft Planetary Computer), relative orbit 62 (ขาลง) + 172 (ขาขึ้น), "
                     f"{int(years[0]) + 543}–{int(years[-1]) + 543} ({years[0]}–{years[-1]})",
           "method": r["method"], "season": "1 ส.ค.–15 ธ.ค. (ระยะเวลาถูกตัดที่ 15 ธ.ค. — ท่วมยาวกว่านั้นนับไม่ถึง)", "note": ca_note,
           "bounds": [[bb[1], bb[0]], [bb[3], bb[2]]], "png": "s1_province_freq.png",
           "png_legend": [[f"rgba({c[0]},{c[1]},{c[2]},{c[3] / 255:.2f})", t] for _, _, c, t in FREQ_CLS] + [["rgba(148,163,184,.47)", "แหล่งน้ำถาวร"]],
           "n_years": len(years), "perm_water_km2": r["perm_water_km2"],
           "prov_ge30d_ge3y_km2": round(sum(t["ge30d_ge3y_km2"] for t in tam), 1),
           "prov_ge60d_ge3y_km2": round(sum(t["ge60d_ge3y_km2"] for t in tam), 1),
           "years": yrs, "district": sorted(dist.values(), key=lambda d: -d["ge30d_ge3y_km2"]), "tambon": tam}
    json.dump(web, open(os.path.join(st, "s1_province.json"), "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))

    cols = ["code", "name", "district", "area_km2", "ge30d_ge3y_km2", "ge30d_ge5y_km2", "ge60d_ge3y_km2", "mean_years_ge30d", "perm_km2"]
    with open(os.path.join(out_dir, "s1_province_tambon.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(cols + [f"ge60d_{y}" for y in years] + [f"max_{y}" for y in years])
        for t in sorted(tam, key=lambda t: -t["ge30d_ge3y_km2"]):
            w.writerow([t[c] for c in cols] + [t["ge60d_km2_by_year"][y] for y in years] + [t["max_km2_by_year"][y] for y in years])
    return web


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--site", default=".")
    a = ap.parse_args(); w = main(a.out, a.site)
    print(w["prov_ge30d_ge3y_km2"], w["prov_ge60d_ge3y_km2"])
