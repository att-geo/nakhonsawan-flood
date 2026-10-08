# -*- coding: utf-8 -*-
"""สร้างชั้นข้อมูล Esri JSON (มี alias ภาษาไทย) จากผลวิเคราะห์ สำหรับ publish ขึ้น ArcGIS Enterprise Portal

    python pipeline/portal_layers.py --site . --out ../portal_out [--live-url https://att-geo.github.io/nakhonsawan-flood]

ผลลัพธ์ (WGS84):
  analysis/  hex_s1, tambon_s1, district_s1, tambon_events, district_events, tambon_scn, district_scn
  live/      hex_status, district_status
ฟังก์ชัน hex_status_rows / district_status_rows ใช้ร่วมกับ portal_sync.py (อัปเดตทุกชั่วโมง)
stdlib อย่างเดียว
"""
import argparse, json, os, urllib.request

YEARS_S1 = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
YEARS_EV = [2021, 2022, 2024, 2025]
SCN = ["A", "C", "D", "E"]
CLS_LABEL = ["ไม่ท่วม", "เฝ้าระวัง", "ท่วมขังเล็กน้อย", "ท่วมขังปานกลาง", "ท่วมสูง"]
DEPTH_WORD = ["ไม่มีน้ำท่วม", "ระดับข้อเท้า", "ระดับเข่า", "ระดับเอว", "สูงกว่าเอว"]
CAUSE = {0: "", 1: "ฝน", 2: "น้ำล้นตลิ่ง", 3: "ฝน + น้ำล้นตลิ่ง"}
BE = lambda y: y + 543


def F(name, alias, typ="d", length=None):
    t = {"s": "esriFieldTypeString", "i": "esriFieldTypeInteger", "d": "esriFieldTypeDouble",
         "t": "esriFieldTypeDate", "h": "esriFieldTypeSmallInteger"}[typ]
    f = {"name": name, "type": t, "alias": alias}
    if typ == "s": f["length"] = length or 64
    return f


# ---------- geometry ----------
def _area(r):
    return sum(r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1] for i in range(len(r) - 1)) / 2.0


def esri_rings(geom):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    rings = []
    for poly in polys:
        for k, ring in enumerate(poly):
            ring = [[round(x, 6), round(y, 6)] for x, y, *_ in ring]
            if ring[0] != ring[-1]: ring.append(ring[0])
            cw = _area(ring) < 0           # Esri: วงนอกตามเข็ม, รูทวนเข็ม
            if (k == 0) != cw: ring = ring[::-1]
            rings.append(ring)
    return {"rings": rings}


def fc(fields, feats, gtype="esriGeometryPolygon"):
    return {"displayFieldName": fields[0]["name"], "geometryType": gtype,
            "spatialReference": {"wkid": 4326, "latestWkid": 4326},
            "fields": fields, "features": feats}


def r2(v, n=2):
    return None if v is None else round(float(v), n)


def load(p):
    return json.load(open(p, encoding="utf-8"))


# ---------- live (ใช้ร่วมกับ portal_sync.py) ----------
HEX_STATUS_FIELDS = [
    F("hid", "รหัส hex", "i"), F("amphoe", "อำเภอ", "s", 40),
    F("depth_cm", "ความลึกตอนนี้ (ซม.)"), F("cls", "ระดับ (0–4)", "h"),
    F("cls_label", "สถานการณ์", "s", 30), F("depth_word", "ความลึกเทียบร่างกาย", "s", 30),
    F("cause", "สาเหตุ", "s", 30), F("flood_pct", "% พื้นที่ hex ที่ท่วม"),
    F("flooded_h", "ท่วมมาแล้ว (ชม.)", "i"), F("remain_h", "คาดว่าจะลดใน (ชม.)", "i"),
    F("remain_txt", "คาดว่าจะลดใน", "s", 30),
    F("max72_cm", "ลึกสูงสุดใน 72 ชม. (ซม.)"), F("river72_cm", "ลึกจากน้ำล้นตลิ่ง 72 ชม. (ซม.)"),
    F("rain24", "ฝน 24 ชม. (มม.)"), F("rain72", "ฝน 72 ชม. (มม.)"), F("rain7d", "ฝน 7 วัน (มม.)"),
    F("fc24", "ฝนพยากรณ์ 24 ชม. (มม.)"), F("fc72", "ฝนพยากรณ์ 72 ชม. (มม.)"),
    F("wse", "ระดับผิวน้ำ (ม.รทก.)"),
    F("hand", "HAND (ม.)"), F("elev", "ระดับพื้นดิน (ม.)"), F("cn", "Curve Number"),
    F("updated", "ข้อมูล ณ", "t"),
    # ค่าดิบสำหรับเว็บแอป (status.json) — เว็บอ่านชั้นนี้แทนไฟล์
    F("src", "รหัสสาเหตุ (0–3)", "h"), F("dm72", "ความลึกเปลี่ยนใน 72 ชม. (ซม.)"), F("ui", "น้ำไหลเข้าจากนอกจังหวัด 72 ชม. (มม.)"),
]


def _arr(st, k, i, default=0):
    a = st.get(k)
    return a[i] if a is not None and i < len(a) else default


def remain_txt(r, cls):
    if cls == 0: return ""
    if r is None: return ""
    if r >= 999: return "นานกว่า 14 วัน"
    if r < 24: return "ภายใน 1 วัน"
    return f"ประมาณ {round(r / 24)} วัน"


def hex_status_rows(status, params, meta):
    """คืน list ของ attributes ราย hex (เรียงตาม hid)"""
    n = status["n"]; t_ms = int(meta["now"]) * 1000
    al = params["amphoe_list"]; rows = []
    for i in range(n):
        c = int(_arr(status, "c", i)); r = _arr(status, "r", i, None); h = _arr(status, "h", i, None)
        rows.append({
            "hid": i, "amphoe": al[params["amph"][i]] if params["amph"][i] is not None and params["amph"][i] >= 0 else "",
            "depth_cm": r2(_arr(status, "d", i), 1), "cls": c, "cls_label": CLS_LABEL[c], "depth_word": DEPTH_WORD[c],
            "cause": CAUSE.get(int(_arr(status, "s", i)), ""), "flood_pct": r2(_arr(status, "f", i), 1),
            "flooded_h": int(h) if h is not None else None,
            "remain_h": int(r) if r is not None else None, "remain_txt": remain_txt(r, c),
            "max72_cm": r2(_arr(status, "m72", i), 1), "river72_cm": r2(_arr(status, "u72", i), 1),
            "rain24": r2(_arr(status, "p24", i), 1), "rain72": r2(_arr(status, "p72", i), 1),
            "rain7d": r2(_arr(status, "p7d", i), 1), "fc24": r2(_arr(status, "f24", i), 1),
            "fc72": r2(_arr(status, "f72", i), 1), "wse": r2(_arr(status, "wse", i, None), 2),
            "hand": r2(params["hand"][i]), "elev": r2(params["elev"][i]), "cn": r2(params["cn"][i], 1),
            "updated": t_ms, "src": int(_arr(status, "s", i)),
            "dm72": r2(_arr(status, "dm72", i, None), 1), "ui": r2(_arr(status, "ui", i, None), 1),
        })
    return rows


DISTRICT_STATUS_FIELDS = [
    F("amphoe", "อำเภอ", "s", 40), F("km2_now", "ท่วมขังตอนนี้ (กม²)"), F("rai_now", "ท่วมขังตอนนี้ (ไร่)", "i"),
    F("km2_watch", "รวมเฝ้าระวัง (กม²)"), F("km2_72h", "ท่วมสูงสุดใน 72 ชม. (กม²)"),
    F("km2_river72", "ท่วมจากน้ำล้นตลิ่ง 72 ชม. (กม²)"),
    F("max_cls_now", "ระดับสูงสุดตอนนี้", "h"), F("max_label", "สถานการณ์สูงสุด", "s", 30),
    F("rain24", "ฝน 24 ชม. เฉลี่ย (มม.)"), F("rain72", "ฝน 72 ชม. เฉลี่ย (มม.)"), F("fc72", "ฝนพยากรณ์ 72 ชม. (มม.)"),
    F("h_med", "ท่วมมาแล้ว มัธยฐาน (ชม.)", "i"), F("r_med", "คาดลดใน มัธยฐาน (ชม.)", "i"),
    F("r_lt1d", "ลดใน < 1 วัน (กม²)"), F("r_1_3d", "ลดใน 1–3 วัน (กม²)"), F("r_3_7d", "ลดใน 3–7 วัน (กม²)"),
    F("r_7_14d", "ลดใน 7–14 วัน (กม²)"), F("r_gt14d", "ลดนานกว่า 14 วัน (กม²)"),
    F("updated", "ข้อมูล ณ", "t"),
]


def district_status_rows(districts, meta):
    t_ms = int(meta["now"]) * 1000; out = {}
    for d in districts:
        rk = (d.get("r_km2") or [0] * 5) + [0] * 5
        mc = int(d.get("max_cls_now") or 0)
        out[d["amphoe"]] = {
            "amphoe": d["amphoe"], "km2_now": r2(d.get("km2_now")), "rai_now": int(round((d.get("km2_now") or 0) * 625)),
            "km2_watch": r2(d.get("km2_watch")), "km2_72h": r2(d.get("km2_72h")), "km2_river72": r2(d.get("km2_river72")),
            "max_cls_now": mc, "max_label": CLS_LABEL[mc], "rain24": r2(d.get("rain24"), 1), "rain72": r2(d.get("rain72"), 1),
            "fc72": r2(d.get("fc72"), 1), "h_med": d.get("h_med"), "r_med": d.get("r_med"),
            "r_lt1d": r2(rk[0]), "r_1_3d": r2(rk[1]), "r_3_7d": r2(rk[2]), "r_7_14d": r2(rk[3]), "r_gt14d": r2(rk[4]),
            "updated": t_ms}
    return out


def fetch_live(site, live_url):
    names = ["status", "meta", "districts"]; out = {}
    for k in names:
        if live_url:
            with urllib.request.urlopen(f"{live_url.rstrip('/')}/data/live/{k}.json", timeout=60) as r:
                out[k] = json.loads(r.read().decode("utf-8"))
        else:
            out[k] = load(os.path.join(site, "data", "live", f"{k}.json"))
    return out


# ---------- analysis ----------
def freq_label(y):
    return "ไม่เคย" if y == 0 else "บางปี (1–2 ปี)" if y <= 2 else "บ่อย (3–5 ปี)" if y <= 5 else "เกือบทุกปี (6–9 ปี)"


def build(site, out, live_url):
    S = lambda *p: os.path.join(site, "data", "static", *p)
    params = load(S("params.json")); hexg = load(S("hex.geojson"))
    tam = load(S("province_tambon.geojson")); dist = load(S("districts.geojson"))
    s1 = load(S("s1_province.json")); s1h = load(S("s1_province_hex.json"))
    ev = load(S("prov_events.json")); scn = load(S("prov_scenarios.json"))
    al = params["amphoe_list"]
    hex_geom = {f["properties"]["i"]: esri_rings(f["geometry"]) for f in hexg["features"]}
    tam_props = [f["properties"] for f in tam["features"]]
    tam_geom = {(p["name"], p["district"]): esri_rings(f["geometry"]) for p, f in zip(tam_props, tam["features"])}
    dist_geom = {f["properties"]["shapeName"]: esri_rings(f["geometry"]) for f in dist["features"]}
    res = {}

    # 1) hex_s1
    fl = [F("hid", "รหัส hex", "i"), F("amphoe", "อำเภอ", "s", 40), F("tambon", "ตำบล", "s", 40),
          F("yrs_ge30d", "จำนวนปีที่ท่วม ≥ 30 วัน (จาก 9 ปี)", "h"), F("freq_label", "ความถี่น้ำขังนาน", "s", 30),
          F("rep_pct", "% พื้นที่ท่วม ≥30 วัน ≥3 ปี"), F("last_year", "ปีล่าสุดที่ท่วมนาน (พ.ศ.)", "i"),
          F("tam_code", "รหัสตำบล", "s", 12)]
    feats = []
    for i in range(s1h["n"]):
        y = int(s1h["yrs"][i]); ti = s1h["tam"][i]
        feats.append({"geometry": hex_geom[i], "attributes": {
            "hid": i, "amphoe": al[params["amph"][i]], "tambon": tam_props[ti]["name"] if ti is not None and 0 <= ti < len(tam_props) else "",
            "yrs_ge30d": y, "freq_label": freq_label(y), "rep_pct": r2(s1h["rep_pct"][i], 1),
            "last_year": BE(s1h["last"][i]) if s1h["last"][i] else None,
            "tam_code": tam_props[ti]["code"] if ti is not None and 0 <= ti < len(tam_props) else ""}})
    res["analysis/hex_s1"] = fc(fl, feats)

    # 2) tambon_s1
    fl = [F("code", "รหัสตำบล", "s", 12), F("tambon", "ตำบล", "s", 40), F("amphoe", "อำเภอ", "s", 40),
          F("tambon_en", "Tambon", "s", 60), F("area_km2", "พื้นที่ (กม²)"),
          F("ge30d_3y", "ท่วม ≥30 วัน อย่างน้อย 3 ปี (กม²)"), F("ge30d_3y_rai", "ท่วม ≥30 วัน อย่างน้อย 3 ปี (ไร่)", "i"),
          F("ge30d_5y", "ท่วม ≥30 วัน อย่างน้อย 5 ปี (กม²)"), F("ge60d_3y", "ท่วม ≥60 วัน อย่างน้อย 3 ปี (กม²)"),
          F("pct_ge30d_3y", "% ของตำบลที่ท่วมนานซ้ำซาก"), F("mean_yrs", "เฉลี่ยกี่ปีที่ท่วม ≥30 วัน"),
          F("perm_km2", "แหล่งน้ำถาวร (กม²)")]
    fl += [F(f"g30_{y}", f"ท่วม ≥30 วัน ปี {BE(y)} (กม²)") for y in YEARS_S1]
    fl += [F(f"g60_{y}", f"ท่วม ≥60 วัน ปี {BE(y)} (กม²)") for y in YEARS_S1]
    fl += [F(f"max_{y}", f"ท่วมสูงสุด ปี {BE(y)} (กม²)") for y in YEARS_S1]
    feats = []
    s1t = {(t["name"], t["district"]): t for t in s1["tambon"]}
    for p in tam_props:
        t = s1t.get((p["name"], p["district"]), {}); a = {
            "code": p["code"], "tambon": p["name"], "amphoe": p["district"], "tambon_en": p.get("name_en", ""),
            "area_km2": r2(p["area_km2"]), "ge30d_3y": r2(t.get("ge30d_ge3y_km2")),
            "ge30d_3y_rai": int(round((t.get("ge30d_ge3y_km2") or 0) * 625)),
            "ge30d_5y": r2(t.get("ge30d_ge5y_km2")), "ge60d_3y": r2(t.get("ge60d_ge3y_km2")),
            "pct_ge30d_3y": r2(100 * (t.get("ge30d_ge3y_km2") or 0) / p["area_km2"], 1) if p["area_km2"] else None,
            "mean_yrs": r2(t.get("mean_years_ge30d")), "perm_km2": r2(t.get("perm_km2"))}
        for y in YEARS_S1:
            a[f"g30_{y}"] = r2((t.get("ge30d_km2_by_year") or {}).get(str(y)))
            a[f"g60_{y}"] = r2((t.get("ge60d_km2_by_year") or {}).get(str(y)))
            a[f"max_{y}"] = r2((t.get("max_km2_by_year") or {}).get(str(y)))
        feats.append({"geometry": tam_geom[(p["name"], p["district"])], "attributes": a})
    res["analysis/tambon_s1"] = fc(fl, feats)

    # 3) district_s1
    fl = [F("amphoe", "อำเภอ", "s", 40), F("n_tambon", "จำนวนตำบล", "h"), F("area_km2", "พื้นที่ (กม²)"),
          F("ge30d_3y", "ท่วม ≥30 วัน อย่างน้อย 3 ปี (กม²)"), F("ge30d_3y_rai", "ท่วม ≥30 วัน อย่างน้อย 3 ปี (ไร่)", "i"),
          F("ge60d_3y", "ท่วม ≥60 วัน อย่างน้อย 3 ปี (กม²)")]
    fl += [F(f"g60_{y}", f"ท่วม ≥60 วัน ปี {BE(y)} (กม²)") for y in YEARS_S1]
    feats = []
    for d in s1["district"]:
        a = {"amphoe": d["district"], "n_tambon": d["n_tambon"], "area_km2": r2(d["area_km2"]),
             "ge30d_3y": r2(d["ge30d_ge3y_km2"]), "ge30d_3y_rai": int(round(d["ge30d_ge3y_km2"] * 625)),
             "ge60d_3y": r2(d["ge60d_ge3y_km2"])}
        for y in YEARS_S1: a[f"g60_{y}"] = r2(d["ge60d_km2_by_year"].get(str(y)))
        feats.append({"geometry": dist_geom[d["district"]], "attributes": a})
    res["analysis/district_s1"] = fc(fl, feats)

    # 3b) ตารางรายปีทั้งจังหวัด (สำหรับกราฟ)
    fl = [F("year_be", "ปี (พ.ศ.)", "i"), F("year_ce", "ปี (ค.ศ.)", "i"), F("n_img", "จำนวนภาพ", "h"),
          F("ge30d_km2", "ท่วม ≥30 วัน ทั้งจังหวัด (กม²)"), F("ge60d_km2", "ท่วม ≥60 วัน ทั้งจังหวัด (กม²)"),
          F("peak_km2", "ท่วมสูงสุด (กม²)"), F("peak_date", "วันที่ท่วมสูงสุด", "s", 12)]
    rows = [{"attributes": {"year_be": BE(int(y)), "year_ce": int(y), "n_img": v["n_img"], "ge30d_km2": r2(v["prov_ge30d_km2"]),
             "ge60d_km2": r2(v["prov_ge60d_km2"]), "peak_km2": r2(v.get("peak_km2")), "peak_date": v.get("peak") or ""}}
            for y, v in sorted(s1["years"].items())]
    res["analysis/year_s1"] = fc(fl, rows, "esriGeometryNull")

    # 4) events
    def ev_fields(prefix_fields):
        f = list(prefix_fields)
        for y in YEARS_EV:
            tag = f"ปี {BE(y)}" + (" (ทดสอบ)" if ev["years"][str(y)].get("test_year") else "")
            f += [F(f"obs_{y}", f"ท่วม ≥30 วัน จริง S1 {tag} (กม²)"), F(f"mod_{y}", f"ท่วม ≥30 วัน แบบจำลอง {tag} (กม²)"),
                  F(f"pod_{y}", f"POD {tag}"), F(f"far_{y}", f"FAR {tag}"), F(f"csi_{y}", f"CSI {tag}")]
        return f + [F("csi_mean", "CSI เฉลี่ย 4 ปี")]

    def ev_attrs(by):
        a = {}; cs = []
        for y in YEARS_EV:
            v = by.get(str(y)) or {}
            for k in ("obs", "mod"): a[f"{k}_{y}"] = r2(v.get(f"{k}_km2"))
            for k in ("pod", "far", "csi"): a[f"{k}_{y}"] = r2(v.get(k))
            if v.get("csi") is not None and (v.get("obs_km2") or 0) + (v.get("mod_km2") or 0) > 0: cs.append(v["csi"])
        a["csi_mean"] = r2(sum(cs) / len(cs)) if cs else None
        return a
    evt = {(t["name"], t["district"]): t for t in ev["tambon"]}
    feats = []
    for p in tam_props:
        a = {"code": p["code"], "tambon": p["name"], "amphoe": p["district"]}
        a.update(ev_attrs((evt.get((p["name"], p["district"])) or {}).get("by_year", {})))
        feats.append({"geometry": tam_geom[(p["name"], p["district"])], "attributes": a})
    res["analysis/tambon_events"] = fc(ev_fields([F("code", "รหัสตำบล", "s", 12), F("tambon", "ตำบล", "s", 40), F("amphoe", "อำเภอ", "s", 40)]), feats)
    feats = []
    for name, g in dist_geom.items():
        a = {"amphoe": name}; a.update(ev_attrs({y: ev["years"][y]["district"].get(name, {}) for y in ev["years"]}))
        feats.append({"geometry": g, "attributes": a})
    res["analysis/district_events"] = fc(ev_fields([F("amphoe", "อำเภอ", "s", 40)]), feats)
    fl = [F("year_be", "ปี (พ.ศ.)", "i"), F("year_ce", "ปี (ค.ศ.)", "i"), F("test_year", "ปีทดสอบ (ไม่ใช้ปรับค่า)", "s", 4),
          F("n_img", "จำนวนภาพ S1", "h"), F("obs_km2", "ท่วม ≥30 วัน จริง (กม²)"), F("mod_km2", "ท่วม ≥30 วัน แบบจำลอง (กม²)"),
          F("pod", "POD"), F("far", "FAR"), F("csi", "CSI"), F("daily_csi", "CSI รายภาพเฉลี่ย"), F("run", "รอบแบบจำลอง", "s", 20)]
    rows = []
    for y in YEARS_EV:
        v = ev["years"][str(y)]; g = v["ge30d"]
        rows.append({"attributes": {"year_be": BE(y), "year_ce": y, "test_year": "ใช่" if v.get("test_year") else "",
                     "n_img": v["n_img"], "obs_km2": r2(g["obs_km2"]), "mod_km2": r2(g["mod_km2"]), "pod": r2(g["pod"]),
                     "far": r2(g["far"]), "csi": r2(g["csi"]), "daily_csi": r2(v.get("daily_csi_mean")), "run": v.get("run", "")}})
    res["analysis/year_events"] = fc(fl, rows, "esriGeometryNull")

    # 5) scenarios
    def scn_fields(pre):
        f = list(pre)
        for X in SCN:
            lb = scn[X]["label"]
            f += [F(f"{X}_patch", f"{X}: ท่วมเป็นผืน (กม²) — {lb}"), F(f"{X}_ge7d", f"{X}: ขัง ≥7 วัน (กม²)"),
                  F(f"{X}_left21", f"{X}: ยังไม่ลดวันที่ 21 (กม²)")]
            if pre[0]["name"] == "code": f.append(F(f"{X}_p95m", f"{X}: ลึก P95 (ม.)"))
        return f
    sct = {X: {(t["name"], t["district"]): t for t in scn[X]["tambon"]} for X in SCN}
    feats = []
    for p in tam_props:
        a = {"code": p["code"], "tambon": p["name"], "amphoe": p["district"]}
        for X in SCN:
            t = sct[X].get((p["name"], p["district"]), {})
            a.update({f"{X}_patch": r2(t.get("patch_km2")), f"{X}_ge7d": r2(t.get("wet_ge7d_km2")),
                      f"{X}_left21": r2(t.get("left_end_km2")), f"{X}_p95m": r2(t.get("depth_p95_m"))})
        feats.append({"geometry": tam_geom[(p["name"], p["district"])], "attributes": a})
    res["analysis/tambon_scn"] = fc(scn_fields([F("code", "รหัสตำบล", "s", 12), F("tambon", "ตำบล", "s", 40), F("amphoe", "อำเภอ", "s", 40)]), feats)
    feats = []
    for name, g in dist_geom.items():
        a = {"amphoe": name}
        for X in SCN:
            d = scn[X]["district"].get(name, {})
            a.update({f"{X}_patch": r2(d.get("patch_km2")), f"{X}_ge7d": r2(d.get("ge7d_km2")), f"{X}_left21": r2(d.get("left_end_km2"))})
        feats.append({"geometry": g, "attributes": a})
    res["analysis/district_scn"] = fc(scn_fields([F("amphoe", "อำเภอ", "s", 40)]), feats)
    fl = [F("scn", "สถานการณ์", "s", 2), F("label", "รายละเอียด", "s", 80), F("rain_mm", "ฝน (มม.)", "i"),
          F("rain_days", "ฝนตกกี่วัน", "h"), F("river", "แม่น้ำ", "s", 10), F("peak_km2", "ท่วมสูงสุด (กม²)"),
          F("patch_km2", "ท่วมเป็นผืน (กม²)"), F("ge7d_km2", "ขัง ≥7 วัน (กม²)"), F("left21_km2", "ยังไม่ลดวันที่ 21 (กม²)")]
    rows = [{"attributes": {"scn": X, "label": scn[X]["label"], "rain_mm": scn[X]["rain"], "rain_days": scn[X]["rain_days"],
             "river": scn[X]["river"], "peak_km2": r2(scn[X]["total"]["peak_km2"]), "patch_km2": r2(scn[X]["total"]["patch_km2"]),
             "ge7d_km2": r2(scn[X]["total"]["ge7d_km2"]), "left21_km2": r2(scn[X]["total"]["left_end_km2"])}} for X in SCN]
    res["analysis/scn_total"] = fc(fl, rows, "esriGeometryNull")

    # 6) live
    L = fetch_live(site, live_url)
    rows = hex_status_rows(L["status"], params, L["meta"])
    res["live/hex_status"] = fc(HEX_STATUS_FIELDS, [{"geometry": hex_geom[r["hid"]], "attributes": r} for r in rows])
    drows = district_status_rows(L["districts"], L["meta"])
    res["live/district_status"] = fc(DISTRICT_STATUS_FIELDS,
                                     [{"geometry": g, "attributes": drows.get(n, {"amphoe": n})} for n, g in dist_geom.items()])

    for k, v in res.items():
        p = os.path.join(out, k + ".json"); os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump(v, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        print(f"{k:28s} {len(v['features']):5d} features  {len(v['fields']):3d} fields  {os.path.getsize(p)/1e6:.2f} MB")
    print("live data at", L["meta"]["generated"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="."); ap.add_argument("--out", default="../portal_out")
    ap.add_argument("--live-url", default="https://att-geo.github.io/nakhonsawan-flood")
    a = ap.parse_args(); build(a.site, a.out, a.live_url)
