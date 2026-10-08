# -*- coding: utf-8 -*-
"""สร้าง Web Map + Dashboard บน Portal จาก service ใน data/static/portal_items.json (รันใน Python ของ ArcGIS Pro)"""
import json, math, os, uuid

def U(): return str(uuid.uuid4())

def merc(lon, lat):
    x = lon * 20037508.34 / 180; y = math.log(math.tan((90 + lat) * math.pi / 360)) * 20037508.34 / math.pi
    return x, y

def rgba(h, a=255):
    h = h.lstrip("#"); return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a]

def sfs(fill, alpha=200, outline="#ffffff", ow=0.4, oa=120):
    return {"type": "esriSFS", "style": "esriSFSSolid", "color": rgba(fill, alpha),
            "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": rgba(outline, oa), "width": ow}}

def uv(field, items, default=None):
    r = {"type": "uniqueValue", "field1": field,
         "uniqueValueInfos": [{"value": str(v), "label": lb, "symbol": s} for v, lb, s in items]}
    if default: r["defaultSymbol"] = default; r["defaultLabel"] = "อื่น ๆ"
    return r

def cb(field, breaks, title=None):
    """breaks: [(max, label, color)] ; ต่ำสุดจาก 0"""
    infos = [{"classMaxValue": mx, "label": lb, "symbol": sfs(c, 210, "#ffffff", 0.5, 160)} for mx, lb, c in breaks]
    return {"type": "classBreaks", "field": field, "minValue": 0, "classBreakInfos": infos,
            "defaultSymbol": sfs("#ffffff", 0, "#9ca3af", 0.4, 140), "defaultLabel": "ไม่มีข้อมูล / 0",
            "legendOptions": {"title": title} if title else {}}

def fi(name, label, digits=None, visible=True):
    f = {"fieldName": name, "label": label, "visible": visible, "isEditable": False}
    if digits is not None: f["format"] = {"places": digits, "digitSeparator": True}
    return f

def popup(title, fields, desc=None, media=None):
    p = {"title": title, "fieldInfos": fields, "showAttachments": False}
    if desc: p["description"] = desc
    else:
        p["popupElements"] = [{"type": "fields"}]
    if media: p["mediaInfos"] = media; p.setdefault("popupElements", []).append({"type": "media"})
    return p

def lyr(title, url, renderer, pop, visible=True, opacity=1, defq=None, labels=None):
    L = {"id": U(), "title": title, "url": url, "layerType": "ArcGISFeatureLayer", "visibility": visible, "opacity": opacity,
         "popupInfo": pop, "layerDefinition": {"drawingInfo": {"renderer": renderer}}}
    if defq: L["layerDefinition"]["definitionExpression"] = defq
    if labels:
        L["layerDefinition"]["drawingInfo"]["labelingInfo"] = labels; L["showLabels"] = True
    return L

def label(expr, minscale=0, size=9, color="#1f2937"):
    return [{"labelExpressionInfo": {"expression": expr}, "labelPlacement": "esriServerPolygonPlacementAlwaysHorizontal",
             "minScale": minscale, "maxScale": 0, "repeatLabel": False,
             "symbol": {"type": "esriTS", "color": rgba(color), "haloColor": rgba("#ffffff"), "haloSize": 1.2,
                        "font": {"family": "Noto Sans Thai", "size": size, "weight": "bold"},
                        "horizontalAlignment": "center", "verticalAlignment": "middle"}}]

YEARS_S1 = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
YEARS_EV = [2021, 2022, 2024, 2025]
BE = lambda y: y + 543


def webmap_json(cfg):
    A = cfg["analysis"]["url"]; Lv = cfg["live"]["url"]; al = cfg["analysis"]["layers"]; at = cfg["analysis"]["tables"]
    ll = cfg["live"]["layers"]
    ops = []
    # อำเภอ (live) — เส้นขอบ + สถิติรายอำเภอ
    ops.append(lyr("อำเภอ — สถานการณ์ล่าสุด", f"{Lv}/{ll['district_status']}",
        {"type": "simple", "symbol": sfs("#ffffff", 0, "#374151", 1.3, 230)},
        popup("อ.{amphoe}", [fi("km2_now", "ท่วมขังตอนนี้ (กม²)", 1), fi("rai_now", "ท่วมขังตอนนี้ (ไร่)", 0),
              fi("km2_72h", "ท่วมสูงสุดใน 72 ชม. (กม²)", 1), fi("km2_river72", "จากน้ำล้นตลิ่ง 72 ชม. (กม²)", 1),
              fi("max_label", "สถานการณ์สูงสุด"), fi("rain24", "ฝน 24 ชม. เฉลี่ย (มม.)", 1), fi("rain72", "ฝน 72 ชม. เฉลี่ย (มม.)", 1),
              fi("fc72", "ฝนพยากรณ์ 72 ชม. (มม.)", 1), fi("r_med", "คาดลดใน มัธยฐาน (ชม.)", 0), fi("updated", "ข้อมูล ณ")]),
        labels=label("'อ.' + $feature.amphoe", 1500000, 10)))
    # สถานการณ์สมมติ
    for X, lbl, vis in [("E", "ฝน 250 มม./5 วัน + แม่น้ำล้นตลิ่ง", False), ("C", "ฝน 250 มม./5 วัน · แม่น้ำปกติ", False)]:
        ops.append(lyr(f"สถานการณ์ {X}: {lbl} — ขัง ≥7 วัน รายตำบล", f"{A}/{al['tambon_scn']}",
            cb(f"{X}_ge7d", [(0.5, "< 0.5 กม²", "#fef9c3"), (2, "0.5–2", "#fde047"), (5, "2–5", "#f59e0b"),
                             (15, "5–15", "#ea580c"), (1000, "> 15 กม²", "#9a3412")], "พื้นที่ขัง ≥ 7 วัน (กม²)"),
            popup("ต.{tambon} อ.{amphoe} — สถานการณ์สมมติ",
                  [fi(f"{x}_{k}", f"{x}: {t}", 2) for x in "ACDE" for k, t in
                   [("patch", "ท่วมเป็นผืน (กม²)"), ("ge7d", "ขัง ≥7 วัน (กม²)"), ("left21", "ยังไม่ลดวันที่ 21 (กม²)")]]
                  + [fi(f"{x}_p95m", f"{x}: ลึก P95 (ม.)", 2) for x in "ACDE"]), visible=vis, opacity=0.85))
    # แบบจำลอง 2D เทียบ S1
    ops.append(lyr("แบบจำลอง 2D เทียบน้ำท่วมจริง — CSI เฉลี่ยรายตำบล", f"{A}/{al['tambon_events']}",
        cb("csi_mean", [(0.2, "< 0.2 (ไม่ตรง)", "#fecaca"), (0.4, "0.2–0.4", "#fde68a"), (0.6, "0.4–0.6", "#a7f3d0"),
                        (1.0, "> 0.6 (ตรงดี)", "#059669")], "CSI (ท่วม ≥ 30 วัน)"),
        popup("ต.{tambon} อ.{amphoe} — แบบจำลอง 2D เทียบ Sentinel-1",
              [fi("csi_mean", "CSI เฉลี่ย", 2)] + [fi(f"{k}_{y}", f"{t} ปี {BE(y)}", 2) for y in YEARS_EV for k, t in
               [("obs", "จริง (กม²)"), ("mod", "จำลอง (กม²)"), ("csi", "CSI")]],
              media=[{"type": "columnchart", "title": "ท่วม ≥ 30 วัน: จริง vs แบบจำลอง (กม²)",
                      "value": {"fields": [f"{k}_{y}" for y in YEARS_EV for k in ("obs", "mod")]}}]),
        visible=False, defq="(obs_2021 + obs_2022 + obs_2024 + obs_2025 + mod_2021 + mod_2022 + mod_2024 + mod_2025) > 0.5"))
    # S1 รายตำบล
    ops.append(lyr("น้ำขังนานซ้ำซาก รายตำบล (ไร่ที่ท่วม ≥30 วัน อย่างน้อย 3 ใน 9 ปี)", f"{A}/{al['tambon_s1']}",
        cb("ge30d_3y_rai", [(300, "< 300 ไร่", "#fef3c7"), (1500, "300–1,500", "#fcd34d"), (5000, "1,500–5,000", "#f97316"),
                            (15000, "5,000–15,000", "#c2410c"), (100000, "> 15,000 ไร่", "#7f1d1d")], "ไร่"),
        popup("ต.{tambon} อ.{amphoe}",
              [fi("ge30d_3y_rai", "ท่วม ≥30 วัน อย่างน้อย 3 ปี (ไร่)", 0), fi("ge30d_3y", "… (กม²)", 2),
               fi("pct_ge30d_3y", "% ของตำบล", 1), fi("ge60d_3y", "ท่วม ≥60 วัน อย่างน้อย 3 ปี (กม²)", 2),
               fi("mean_yrs", "เฉลี่ยกี่ปีที่ท่วม ≥30 วัน", 1), fi("area_km2", "พื้นที่ตำบล (กม²)", 1)]
              + [fi(f"g30_{y}", f"ปี {BE(y)}", 2) for y in YEARS_S1],
              media=[{"type": "columnchart", "title": "พื้นที่ท่วม ≥ 30 วัน รายปี (กม²)",
                      "value": {"fields": [f"g30_{y}" for y in YEARS_S1]}}]),
        visible=False, opacity=0.9, labels=label("$feature.tambon", 400000, 8)))
    # S1 ราย hex
    ops.append(lyr("ในอดีต ที่ไหนน้ำขังนาน? (ท่วม ≥30 วัน, Sentinel-1 2560–2568)", f"{A}/{al['hex_s1']}",
        uv("freq_label", [("บางปี (1–2 ปี)", "บางปี (1–2 ใน 9 ปี)", sfs("#fdd34d", 170, "#fdd34d", 0, 0)),
                          ("บ่อย (3–5 ปี)", "บ่อย (3–5 ใน 9 ปี)", sfs("#f97316", 200, "#f97316", 0, 0)),
                          ("เกือบทุกปี (6–9 ปี)", "เกือบทุกปี (6–9 ใน 9 ปี)", sfs("#991b1b", 225, "#991b1b", 0, 0))]),
        popup("น้ำขังนานในอดีต — ต.{tambon} อ.{amphoe}",
              [fi("yrs_ge30d", "จำนวนปีที่ท่วม ≥ 30 วัน (จาก 9 ปี)", 0), fi("freq_label", "ความถี่"),
               fi("rep_pct", "% พื้นที่ที่ท่วมนานซ้ำ ≥ 3 ปี", 1), fi("last_year", "ปีล่าสุดที่ท่วมนาน (พ.ศ.)", 0)]),
        visible=True, opacity=0.8, defq="yrs_ge30d > 0"))
    # live hex
    blue = [("1", "เฝ้าระวัง (10–25 ซม. · ข้อเท้า)", "#7dd3fc"), ("2", "ท่วมขังเล็กน้อย (25–50 ซม. · เข่า)", "#38bdf8"),
            ("3", "ท่วมขังปานกลาง (50–100 ซม. · เอว)", "#2563eb"), ("4", "ท่วมสูง (> 1 ม. · สูงกว่าเอว)", "#1e1b4b")]
    ops.append(lyr("น้ำท่วมขังตอนนี้ ราย hex 1 กม² (อัปเดตทุกชั่วโมง)", f"{Lv}/{ll['hex_status']}",
        uv("cls", [(v, lb, sfs(c, 220, "#1e3a8a", 0.3, 120)) for v, lb, c in blue]),
        popup("{cls_label} — {depth_word}",
              [fi("amphoe", "อำเภอ"), fi("depth_cm", "ความลึกตอนนี้ (ซม.)", 0), fi("cause", "สาเหตุ"),
               fi("flood_pct", "% พื้นที่ hex ที่ท่วม", 0), fi("flooded_h", "ท่วมมาแล้ว (ชม.)", 0),
               fi("remain_txt", "คาดว่าจะลดใน"), fi("max72_cm", "ลึกสูงสุดใน 72 ชม. (ซม.)", 0),
               fi("rain24", "ฝน 24 ชม. (มม.)", 0), fi("rain7d", "ฝน 7 วัน (มม.)", 0), fi("fc72", "ฝนพยากรณ์ 72 ชม. (มม.)", 0),
               fi("hand", "ความสูงเหนือลำน้ำ HAND (ม.)", 1), fi("updated", "ข้อมูล ณ")]),
        visible=True, defq="cls > 0"))
    tables = [{"id": U(), "title": "พื้นที่ท่วมนานรายปี ทั้งจังหวัด (Sentinel-1)", "url": f"{A}/{at['year_s1']}"},
              {"id": U(), "title": "แบบจำลอง 2D เทียบน้ำท่วมจริง รายปี", "url": f"{A}/{at['year_events']}"},
              {"id": U(), "title": "สถานการณ์สมมติ A/C/D/E ทั้งจังหวัด", "url": f"{A}/{at['scn_total']}"}]
    x0, y0 = merc(99.08, 15.05); x1, y1 = merc(100.84, 16.34)
    return {
        "operationalLayers": ops, "tables": tables,
        "baseMap": {"title": "Light Gray Canvas", "baseMapLayers": [
            {"id": U(), "title": "World Light Gray Base", "layerType": "ArcGISTiledMapServiceLayer", "visibility": True, "opacity": 1,
             "url": "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer"},
            {"id": U(), "title": "World Light Gray Reference", "layerType": "ArcGISTiledMapServiceLayer", "visibility": True,
             "opacity": 1, "isReference": True,
             "url": "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer"}]},
        "spatialReference": {"wkid": 102100, "latestWkid": 3857},
        "initialState": {"viewpoint": {"targetGeometry": {"xmin": x0, "ymin": y0, "xmax": x1, "ymax": y1,
                                                          "spatialReference": {"wkid": 102100}}}},
        "authoringApp": "NakhonSawanFlood portal_webmap.py", "authoringAppVersion": "1.0", "version": "2.31"}, (x0, y0, x1, y1)


# ---------------- Dashboard ----------------
def _ds(item, layer, stats=None, group=None, order=None, where=None, maxf=None, outf=None):
    d = {"type": "serviceDataset", "name": "main", "dataSource": {"type": "itemDataSource", "itemId": item, "layerId": layer},
         "groupByFields": group or [], "orderByFields": order or [], "statisticDefinitions": stats or [],
         "clientSideStatistics": False, "outFields": outf or ["*"], "returnDistinctValues": False, "allowDownload": True}
    if maxf: d["maxFeatures"] = maxf
    if where:
        d["filter"] = None; d["querySql"] = where  # fallback; หลัก ๆ ใช้ statistic บนทั้งชั้น
    return d

NODATA = {"noDataState": {"verticalAlignment": "middle", "showCaption": True, "showDescription": True},
          "noFilterState": {"verticalAlignment": "middle", "showCaption": True, "showDescription": True}}

def indicator(title, item, layer, field, stat="sum", color="#1d4ed8", unit="", digits=1, live=True):
    w = {"id": U(), "name": title, "showLastUpdate": live, **NODATA,
         "datasets": [_ds(item, layer, [{"onStatisticField": field, "outStatisticFieldName": "value", "statisticType": stat}])],
         "type": "indicatorWidget",
         "defaultSettings": {"textColor": color, "caption": f"<p style=\"text-align:center\"><strong>{title}</strong></p>",
                             "topSection": {"fontSize": 80, "textInfo": {}},
                             "middleSection": {"fontSize": 160, "textInfo": {"text": "{value}"}},
                             "bottomSection": {"fontSize": 80, "textInfo": {"text": unit}}},
         "comparison": "none", "valueType": "statistic",
         "valueFormat": {"name": "value", "prefix": True, "style": "decimal", "useGrouping": True, "maximumFractionDigits": digits},
         "percentageFormat": {"name": "percentage", "prefix": False, "style": "percent", "useGrouping": True},
         "ratioFormat": {"name": "ratio", "prefix": False, "style": "decimal", "useGrouping": True, "maximumFractionDigits": 2},
         "noValueState": {"verticalAlignment": "middle", "showCaption": True, "showDescription": True}}
    return w

def serial(title, item, layer, cat, series, rotate=False, order=None, live=False, stack="none"):
    """series: [(field, label, color)] — groupByValues + sum ต่อชุดข้อมูล (ทุกหมวดมี 1 แถว จึงเท่ากับค่าเดิม)"""
    stats = [{"onStatisticField": f, "outStatisticFieldName": "value", "statisticType": "sum"}
             for f, lb, c in series[:1]]   # Dashboards รองรับ grouped values ทีละ 1 series ใน JSON แบบนี้
    graphs = [{"lineColorField": "_lineColor_", "fillColorsField": "_fillColor_", "type": "column", "fillAlphas": 1,
               "lineAlpha": 1, "lineThickness": 1, "bullet": "none", "bulletAlpha": 1, "bulletBorderAlpha": 0,
               "bulletBorderThickness": 2, "showBalloon": True, "bulletSize": 8, "connect": True,
               "valueField": st["outStatisticFieldName"], "title": lb, "lineColor": c}
              for st, (f, lb, c) in zip(stats, series)]
    if order is None: order = [f"{cat} ASC"]
    w = {"id": U(), "name": title, "caption": f"<p><strong>{title}</strong></p>", "showLastUpdate": live, **NODATA,
         "datasets": [{"type": "serviceDataset", "name": "main", "dataSource": {"type": "itemDataSource", "itemId": item, "layerId": layer},
                       "groupByFields": [cat], "orderByFields": order, "statisticDefinitions": stats,
                       "clientSideStatistics": False, "outFields": ["*"], "returnDistinctValues": False, "allowDownload": True}],
         "selectionMode": "single", "categoryType": "groupByValues", "type": "serialChartWidget",
         "category": {"fieldName": cat, "nullLabel": "null", "blankLabel": "blank", "defaultColor": "#d6d6d6",
                      "nullColor": "#d6d6d6", "blankColor": "#d6d6d6", "labelOverrides": [], "byCategoryColors": False,
                      "labelsPlacement": "default", "labelRotation": 0},
         "valueFormat": {"name": "value", "prefix": True, "style": "decimal", "useGrouping": True, "maximumFractionDigits": 1},
         "labelFormat": {"name": "label", "prefix": True, "style": "decimal", "useGrouping": False, "maximumFractionDigits": 0},
         "datePeriodPatterns": [{"period": "ss", "pattern": "HH:mm:ss"}, {"period": "mm", "pattern": "HH:mm"},
                                {"period": "hh", "pattern": "HH:mm"}, {"period": "DD", "pattern": "MMM d"},
                                {"period": "MM", "pattern": "MMM"}, {"period": "YYYY", "pattern": "yyyy"}],
         "chartScrollbar": {"enabled": False, "dragIcon": "dragIconRoundSmall", "dragIconHeight": 20, "dragIconWidth": 20,
                            "scrollbarHeight": 15},
         "categoryAxis": {"title": "", "titleRotation": 0, "gridPosition": "start", "gridThickness": 1, "gridAlpha": 0.15,
                          "axisThickness": 1, "axisAlpha": 0.5, "labelsEnabled": True, "parseDates": False, "minPeriod": "DD"},
         "valueAxis": {"title": "กม²", "titleRotation": 270, "gridThickness": 1, "gridAlpha": 0.15, "axisThickness": 1,
                       "axisAlpha": 0.5, "labelsEnabled": True, "stackType": stack, "integersOnly": False, "logarithmic": False, "minimum": 0},
         "legend": {"enabled": len(series) > 1, "position": "bottom", "markerSize": 12, "markerType": "square", "align": "center",
                    "labelWidth": 140, "valueWidth": 0},
         "seriesOrderByFields": [], "graphs": graphs, "guides": [],
         "splitBy": {"defaultColor": "#d6d6d6", "seriesProperties": []}, "rotate": rotate,
         "commonGraphProperties": {"lineColorField": "_lineColor_", "fillColorsField": "_fillColor_", "type": "column",
                                   "fillAlphas": 1, "lineAlpha": 1, "lineThickness": 1, "bullet": "none", "bulletAlpha": 1,
                                   "bulletBorderAlpha": 0, "bulletBorderThickness": 2, "showBalloon": True, "bulletSize": 8,
                                   "connect": True}}
    return w

def listw(title, item, layer, order, text, maxf=30, events=None):
    w = {"id": U(), "name": title, "caption": f"<p><strong>{title}</strong></p>", "showLastUpdate": False, **NODATA,
         "datasets": [_ds(item, layer, order=order, maxf=maxf)], "type": "listWidget", "selectionMode": "single",
         "iconType": "none", "text": text}
    if events: w["events"] = events
    return w


def dashboard_json(cfg, webmap_id):
    Ai = cfg["analysis"]["item"]; Li = cfg["live"]["item"]; al = cfg["analysis"]["layers"]; at = cfg["analysis"]["tables"]
    ll = cfg["live"]["layers"]
    mapw = {"id": U(), "name": "แผนที่", **NODATA, "type": "mapWidget", "flashRepeats": 3, "itemId": webmap_id,
            "mapTools": [{"type": "layerVisibilityTool"}, {"type": "searchTool"}], "showNavigation": True,
            "showPopup": True, "scalebarStyle": "line", "showLastUpdate": False}
    legend = {"id": U(), "name": "คำอธิบายสัญลักษณ์", **NODATA, "type": "legendWidget", "mapWidgetId": mapw["id"],
              "caption": "", "showLastUpdate": False}
    i1 = indicator("ท่วมขังตอนนี้ (ทั้งจังหวัด)", Li, ll["district_status"], "km2_now", unit="ตร.กม.", color="#1d4ed8")
    i2 = indicator("ท่วมสูงสุดใน 72 ชม. ข้างหน้า", Li, ll["district_status"], "km2_72h", unit="ตร.กม.", color="#7c3aed")
    i3 = indicator("ฝนพยากรณ์ 72 ชม. สูงสุดรายอำเภอ", Li, ll["district_status"], "fc72", stat="max", unit="มม.", color="#0891b2")
    i4 = indicator("ที่ดินท่วมนานซ้ำซาก (≥30 วัน ≥3 ใน 9 ปี)", Ai, al["district_s1"], "ge30d_3y_rai", unit="ไร่",
                   color="#b91c1c", digits=0, live=False)
    i4["valueFormat"]["prefix"] = False
    c1 = serial("รายอำเภอ: พื้นที่ท่วมขังสูงสุดใน 72 ชม. ข้างหน้า (กม²)", Li, ll["district_status"], "amphoe",
                [("km2_72h", "สูงสุดใน 72 ชม.", "#6d28d9")], rotate=True, order=["value DESC"], live=True)
    c2 = serial("พื้นที่ท่วม ≥ 30 วัน รายปี ทั้งจังหวัด — Sentinel-1 (กม²)", Ai, at["year_s1"], "year_be",
                [("ge30d_km2", "ท่วม ≥ 30 วัน", "#ea580c")])
    c3 = serial("แบบจำลอง 2D: ความแม่น CSI รายปี (ท่วม ≥ 30 วัน ; 2567 = ปีทดสอบ)", Ai, at["year_events"], "year_be",
                [("csi", "CSI", "#0f766e")])
    c3["valueAxis"]["title"] = "CSI"
    c4 = serial("สถานการณ์สมมติ: พื้นที่ขัง ≥ 7 วัน ทั้งจังหวัด (กม²) — A 150 มม. · C 250 มม. · D +เต็มตลิ่ง · E +ล้นตลิ่ง",
                Ai, at["scn_total"], "scn", [("ge7d_km2", "ขัง ≥ 7 วัน", "#9a3412")])
    lst = listw("ตำบลที่น้ำขังนานซ้ำซากมากที่สุด", Ai, al["tambon_s1"], ["ge30d_3y DESC"],
                "<p><strong>ต.{tambon}</strong> อ.{amphoe}<br>ท่วม ≥30 วัน ≥3 ปี: <strong>{ge30d_3y_rai}</strong> ไร่ "
                "({pct_ge30d_3y}% ของตำบล)</p>", maxf=25)
    lst["events"] = [{"type": "selectionChanged", "actions": [{"type": "zoom", "targets": [{"targetId": mapw["id"]}]}]}]
    header = {"type": "header", "title": "นครสวรรค์ — เฝ้าระวังน้ำท่วม 2569",
              "subtitle": "สถานการณ์ล่าสุด (อัปเดตทุกชั่วโมง) · น้ำขังนานในอดีตจาก Sentinel-1 · แบบจำลอง 2D · สถานการณ์สมมติ",
              "textColor": "#ffffff", "backgroundColor": "#0f2a4a", "titleTextColor": "#ffffff", "subtitlePlacement": "below",
              "logoSize": "small", "showMargin": True, "selectors": [], "showSignOutMenu": False,
              "menuLinks": [{"label": "เว็บแอปฉบับประชาชน", "url": "https://att-geo.github.io/nakhonsawan-flood/"}]}
    widgets = [i1, i2, i3, i4, mapw, legend, c1, lst, c2, c3, c4]
    E = lambda w, h, wid: {"width": w, "height": h, "type": "itemLayoutElement", "id": wid}
    S = lambda w, h, els, o: {"width": w, "height": h, "elements": els, "type": "stackLayoutElement", "orientation": o}
    layout = {"rootElement": S(1, 1, [
        S(1, 0.16, [E(0.25, 1, i1["id"]), E(0.25, 1, i2["id"]), E(0.25, 1, i3["id"]), E(0.25, 1, i4["id"])], "col"),
        S(1, 0.84, [
            S(0.22, 1, [E(1, 0.5, c1["id"]), E(1, 0.5, lst["id"])], "row"),
            S(0.53, 1, [E(1, 0.82, mapw["id"]), E(1, 0.18, legend["id"])], "row"),
            S(0.25, 1, [E(1, 0.34, c2["id"]), E(1, 0.33, c3["id"]), E(1, 0.33, c4["id"])], "row")], "col")], "row")}
    return {"version": 47, "authoringApp": "ArcGIS Dashboards", "authoringAppVersion": "4.23.0", "header": header,
            "widgets": widgets, "settings": {"maxPaginationRecords": 50000, "allowElementResizing": False,
                                             "allowElementExpansion": True},
            "mapOverrides": {"highlightColor": "#ff00ff", "trackedFeatureRadius": 60}, "theme": "light",
            "themeOverrides": {}, "numberPrefixOverrides": NUMBER_PREFIX, "layout": layout}


NUMBER_PREFIX = [{"key": "yotta", "symbol": "Y", "enabled": True}, {"key": "zeta", "symbol": "Z", "enabled": True}, {"key": "exa", "symbol": "E", "enabled": True}, {"key": "peta", "symbol": "P", "enabled": True}, {"key": "tera", "symbol": "T", "enabled": True}, {"key": "giga", "symbol": "G", "enabled": True}, {"key": "mega", "symbol": "M", "enabled": True}, {"key": "kilo", "symbol": "k", "enabled": True}, {"key": "base", "symbol": "", "enabled": True}, {"key": "deci", "symbol": "d", "enabled": False}, {"key": "centi", "symbol": "c", "enabled": False}, {"key": "milli", "symbol": "m", "enabled": False}, {"key": "micro", "symbol": "µ", "enabled": False}, {"key": "nano", "symbol": "n", "enabled": False}]


def create(site, share="everyone", folder="NakhonSawan Flood 2026"):
    from arcgis.gis import GIS
    gis = GIS("pro"); p = os.path.join(site, "data", "static", "portal_items.json")
    cfg = json.load(open(p, encoding="utf-8"))
    wm, ext = webmap_json(cfg)
    tags = "นครสวรรค์,น้ำท่วม,flood,Nakhon Sawan,NakhonsawanFlood2026"
    x0, y0, x1, y1 = ext
    props = {"type": "Web Map", "title": "นครสวรรค์ น้ำท่วม 2569 — แผนที่", "tags": tags, "text": json.dumps(wm, ensure_ascii=False),
             "snippet": "สถานการณ์น้ำท่วมขังล่าสุด (รายชั่วโมง) + น้ำขังนานซ้ำซากจาก Sentinel-1 2560–2568 + แบบจำลอง 2D + สถานการณ์สมมติ",
             "extent": "99.08,15.05,100.84,16.34", "typeKeywords": "ArcGIS Online,Explorer Web Map,Map,Online Map,Web Map"}
    fld = gis.content.folders.get(folder)
    old = cfg.get("webmap")
    if old and gis.content.get(old):
        it = gis.content.get(old); it.update(item_properties=props)
    else:
        job = fld.add(props); it = job.result() if hasattr(job, "result") else job
    it.sharing.sharing_level = share; cfg["webmap"] = it.id
    dj = dashboard_json(cfg, it.id)
    dprops = {"type": "Dashboard", "title": "นครสวรรค์ น้ำท่วม 2569 — Dashboard", "tags": tags,
              "text": json.dumps(dj, ensure_ascii=False), "typeKeywords": "Dashboard,Operations Dashboard",
              "snippet": "สรุปสถานการณ์น้ำท่วมจังหวัดนครสวรรค์ รายอำเภอ/ตำบล"}
    oldd = cfg.get("dashboard")
    if oldd and gis.content.get(oldd):
        d = gis.content.get(oldd); d.update(item_properties=dprops)
    else:
        job = fld.add(dprops); d = job.result() if hasattr(job, "result") else job
    d.sharing.sharing_level = share; cfg["dashboard"] = d.id
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    base = cfg["portal"].rstrip("/")
    return {"webmap": f"{base}/home/item.html?id={it.id}", "mapviewer": f"{base}/apps/mapviewer/index.html?webmap={it.id}",
            "dashboard": f"{base}/apps/dashboards/{d.id}"}
