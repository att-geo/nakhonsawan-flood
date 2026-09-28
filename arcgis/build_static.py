# -*- coding: utf-8 -*-
"""
Nakhon Sawan Flood Watch — static layer builder (run inside ArcGIS Pro 3.x, needs Spatial Analyst)

สร้างชั้นข้อมูลคงที่ที่ pipeline near-realtime ต้องใช้:
  DEM (Copernicus GLO-30) -> Fill / Flow Direction / Flow Accumulation / HAND / Sink depth / Slope
  ESA WorldCover 2021      -> Curve Number (HSG C/D ตาม HAND) + สัดส่วนนา/เมือง/น้ำ
  Hex grid 1 km2           -> สถิติราย hex + hex ท้ายน้ำ (downstream) สำหรับ routing
แล้ว export เป็นไฟล์ให้ web app:
  data/static/hex.geojson, params.json, districts.geojson, province.geojson,
  susceptibility.png (+ .json bounds), stations_ref.json

ใช้งาน:  import build_static; build_static.build(project_dir, repo_dir)
หรือผ่าน toolbox  arcgis/NakhonSawanFlood.pyt  (tool: 1) Build static layers)
"""
import os, json, math, urllib.request
import numpy as np
import arcpy
from arcpy.sa import (Fill, FlowDirection, FlowAccumulation, FlowDistance, Con, Raster,
                      Slope, Reclassify, RemapValue, ExtractByMask)

UTM = arcpy.SpatialReference(32647)   # WGS 84 / UTM zone 47N
WGS = arcpy.SpatialReference(4326)
WEBM = arcpy.SpatialReference(3857)
CELL = 30.0

AMPHOE_TH = {
    "Mueang Nakhon Sawan": "เมืองนครสวรรค์", "Krok Phra": "โกรกพระ", "Chum Saeng": "ชุมแสง",
    "Nong Bua": "หนองบัว", "Banphot Phisai": "บรรพตพิสัย", "Kao Liao": "เก้าเลี้ยว",
    "Takhli": "ตาคลี", "Tha Tako": "ท่าตะโก", "Phaisali": "ไพศาลี", "Phayuha Khiri": "พยุหะคีรี",
    "Lat Yao": "ลาดยาว", "Tak Fa": "ตากฟ้า", "Mae Wong": "แม่วงก์", "Mae Poen": "แม่เปิน",
    "Chum Ta Bong": "ชุมตาบง",
}

# ESA WorldCover class -> (CN for HSG C, CN for HSG D)  [USDA TR-55, adapted for Thai paddy]
WC_CN = {10: (70, 77), 20: (77, 83), 30: (74, 80), 40: (82, 86), 50: (90, 93), 60: (86, 89),
         70: (98, 98), 80: (98, 98), 90: (85, 88), 95: (80, 84), 100: (80, 84)}

SOURCES = {
    "adm1": "https://github.com/wmgeolab/geoBoundaries/raw/main/releaseData/gbOpen/THA/ADM1/geoBoundaries-THA-ADM1.geojson",
    "adm2": "https://github.com/wmgeolab/geoBoundaries/raw/main/releaseData/gbOpen/THA/ADM2/geoBoundaries-THA-ADM2_simplified.geojson",
    "wc": "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N15E099_Map.tif",
    "cop": "https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif",
}


def _log(msg, log=None):
    (log or print)(msg)


def download_sources(src, log=None):
    os.makedirs(src, exist_ok=True)
    files = {"tha_adm1.geojson": SOURCES["adm1"], "tha_adm2.geojson": SOURCES["adm2"],
             "wc_N15E099.tif": SOURCES["wc"]}
    for la in (15, 16):
        for lo in (99, 100):
            n = f"Copernicus_DSM_COG_10_N{la}_00_E{lo:03d}_00_DEM"
            files[f"cop_N{la}E{lo:03d}.tif"] = SOURCES["cop"].format(n=n)
    for fn, url in files.items():
        p = os.path.join(src, fn)
        if not os.path.exists(p):
            _log(f"download {fn}", log)
            urllib.request.urlretrieve(url, p + ".part")
            os.replace(p + ".part", p)
    return src


def _gj_filter(in_json, out_json, pred):
    gj = json.load(open(in_json, encoding="utf8"))
    gj["features"] = [f for f in gj["features"] if pred(f["properties"])]
    gj.pop("crs", None)
    json.dump(gj, open(out_json, "w", encoding="utf8"), ensure_ascii=False)
    return out_json


def build(project_dir, repo_dir, hex_km2=1.0, stream_km2=5.0, major_km2=1000.0, log=None):
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    src = download_sources(os.path.join(project_dir, "source"), log)
    gdb = os.path.join(project_dir, "flood_static.gdb")
    if not arcpy.Exists(gdb):
        arcpy.management.CreateFileGDB(project_dir, "flood_static.gdb")
    ws = os.path.join(project_dir, "static_rasters")
    os.makedirs(ws, exist_ok=True)
    out_static = os.path.join(repo_dir, "data", "static")
    os.makedirs(out_static, exist_ok=True)
    arcpy.env.workspace = gdb

    # ---------- 1. boundaries ----------
    _log("1/8 boundaries", log)
    j1 = _gj_filter(os.path.join(src, "tha_adm1.geojson"), os.path.join(src, "ns_adm1.geojson"),
                    lambda p: p["shapeName"].startswith("Nakhon Sawan"))
    j2 = _gj_filter(os.path.join(src, "tha_adm2.geojson"), os.path.join(src, "ns_adm2.geojson"),
                    lambda p: p["shapeName"] in AMPHOE_TH)
    arcpy.conversion.JSONToFeatures(j1, os.path.join(gdb, "prov_wgs"), "POLYGON")
    arcpy.conversion.JSONToFeatures(j2, os.path.join(gdb, "amphoe_wgs"), "POLYGON")
    arcpy.management.Project(os.path.join(gdb, "prov_wgs"), os.path.join(gdb, "prov"), UTM)
    arcpy.management.Project(os.path.join(gdb, "amphoe_wgs"), os.path.join(gdb, "amphoe"), UTM)
    arcpy.analysis.PairwiseBuffer(os.path.join(gdb, "prov"), os.path.join(gdb, "prov_buf"), "3 Kilometers")

    # ---------- 2. DEM ----------
    _log("2/8 DEM mosaic + project", log)
    tiles = [os.path.join(src, f) for f in sorted(os.listdir(src)) if f.startswith("cop_") and f.endswith(".tif")]
    dem_path = os.path.join(ws, "dem.tif")
    if not arcpy.Exists(dem_path):
        arcpy.management.MosaicToNewRaster(tiles, ws, "dem_wgs.tif", WGS, "32_BIT_FLOAT", None, 1, "MEAN")
        arcpy.management.ProjectRaster(os.path.join(ws, "dem_wgs.tif"), os.path.join(ws, "dem_utm.tif"),
                                       UTM, "BILINEAR", f"{CELL} {CELL}")
        ExtractByMask(os.path.join(ws, "dem_utm.tif"), os.path.join(gdb, "prov_buf")).save(dem_path)
    arcpy.env.snapRaster = dem_path
    arcpy.env.extent = dem_path
    arcpy.env.cellSize = dem_path

    # ---------- 3. hydrology ----------
    _log("3/8 Fill / FlowDir / FlowAcc / HAND", log)
    p = lambda n: os.path.join(ws, n + ".tif")
    if not arcpy.Exists(p("hand")):
        fill = Fill(dem_path); fill.save(p("fill"))
        fdir = FlowDirection(fill, "NORMAL", None, "D8"); fdir.save(p("fdir"))
        facc = FlowAccumulation(fdir, None, "FLOAT", "D8"); facc.save(p("facc"))
        cell_km2 = CELL * CELL / 1e6
        Con(facc > stream_km2 / cell_km2, 1).save(p("stream"))
        Con(facc > major_km2 / cell_km2, 1).save(p("major"))
        FlowDistance(p("stream"), fill, fdir, "VERTICAL", "D8").save(p("hand"))
        FlowDistance(p("major"), fill, fdir, "VERTICAL", "D8").save(p("hand_major"))
        (fill - Raster(dem_path)).save(p("sink"))
        Slope(dem_path, "PERCENT_RISE").save(p("slope"))

    # ---------- 4. land cover -> CN ----------
    _log("4/8 WorldCover -> Curve Number", log)
    if not arcpy.Exists(p("cn")):
        ext = arcpy.Describe(os.path.join(gdb, "prov_buf")).extent.projectAs(WGS)
        arcpy.management.Clip(os.path.join(src, "wc_N15E099.tif"),
                              f"{ext.XMin} {ext.YMin} {ext.XMax} {ext.YMax}", p("wc_clip"))
        arcpy.management.ProjectRaster(p("wc_clip"), p("wc"), UTM, "NEAREST", f"{CELL} {CELL}")
        cnC = Reclassify(p("wc"), "Value", RemapValue([[k, v[0]] for k, v in WC_CN.items()]), "NODATA")
        cnD = Reclassify(p("wc"), "Value", RemapValue([[k, v[1]] for k, v in WC_CN.items()]), "NODATA")
        Con(Raster(p("hand")) < 5, cnD, cnC).save(p("cn"))

    # ---------- 5. hex grid ----------
    _log("5/8 hex grid", log)
    hex_all = os.path.join(gdb, "hex_tess_" + str(int(__import__("time").time()))); hexfc = os.path.join(gdb, "hex")
    ext = arcpy.Describe(os.path.join(gdb, "prov")).extent
    for fc in ("hex_lyr", hexfc, os.path.join(gdb, "hex_sj"), os.path.join(gdb, "hex_sj2")):
        if arcpy.Exists(fc):
            arcpy.management.Delete(fc)
    arcpy.management.GenerateTessellation(hex_all, ext, "HEXAGON", f"{hex_km2} SquareKilometers", UTM)
    lyr = arcpy.management.MakeFeatureLayer(hex_all, "hex_lyr")
    arcpy.management.SelectLayerByLocation(lyr, "HAVE_THEIR_CENTER_IN", os.path.join(gdb, "prov"))
    arcpy.management.CopyFeatures(lyr, hexfc)
    arcpy.management.Delete(lyr)
    arcpy.management.AddField(hexfc, "hid", "LONG")
    arcpy.management.AddField(hexfc, "amphoe", "TEXT", field_length=64)
    with arcpy.da.UpdateCursor(hexfc, ["hid"], sql_clause=(None, "ORDER BY GRID_ID")) as cur:
        for i, row in enumerate(cur):
            cur.updateRow([i])
    sj = os.path.join(gdb, "hex_sj")
    arcpy.analysis.SpatialJoin(hexfc, os.path.join(gdb, "amphoe"), sj, "JOIN_ONE_TO_ONE", "KEEP_ALL",
                               match_option="LARGEST_OVERLAP")
    amap = {r[0]: r[1] for r in arcpy.da.SearchCursor(sj, ["hid", "shapeName"])}
    if any(not v for v in amap.values()):          # ขอบ hex ที่ไม่ทับอำเภอ -> ใช้อำเภอที่ใกล้ที่สุด
        sj2 = os.path.join(gdb, "hex_sj2")
        arcpy.analysis.SpatialJoin(hexfc, os.path.join(gdb, "amphoe"), sj2, "JOIN_ONE_TO_ONE", "KEEP_ALL",
                                   match_option="CLOSEST")
        for hid_, nm in arcpy.da.SearchCursor(sj2, ["hid", "shapeName"]):
            if not amap.get(hid_):
                amap[hid_] = nm
    with arcpy.da.UpdateCursor(hexfc, ["hid", "amphoe"]) as cur:
        for hid, _ in cur:
            cur.updateRow([hid, AMPHOE_TH.get(amap.get(hid) or "", amap.get(hid) or "")])
    arcpy.conversion.PolygonToRaster(hexfc, "hid", p("hexid"), "CELL_CENTER", "", CELL)

    # ---------- 6. zonal stats (numpy) ----------
    _log("6/8 zonal statistics + downstream tracing", log)
    ref = arcpy.Raster(dem_path)
    ll = arcpy.Point(ref.extent.XMin, ref.extent.YMin)
    nrows, ncols = ref.height, ref.width

    def arr(name, nod=np.nan):
        return arcpy.RasterToNumPyArray(p(name), ll, ncols, nrows, nod)

    hid = arr("hexid", -1).astype(np.int32)
    N = int(hid.max()) + 1
    m = hid >= 0
    z = hid[m]
    cnt = np.bincount(z, minlength=N).astype(float)

    def zmean(a):
        v = a[m].astype(float); ok = ~np.isnan(v)
        s = np.bincount(z[ok], weights=v[ok], minlength=N); c = np.bincount(z[ok], minlength=N)
        return np.where(c > 0, s / np.maximum(c, 1), np.nan)

    def zpct(a, q):
        v = a[m].astype(float)
        order = np.lexsort((v, z)); zs = z[order]; vs = v[order]
        starts = np.searchsorted(zs, np.arange(N)); ends = np.searchsorted(zs, np.arange(N), "right")
        out = np.full(N, np.nan)
        for i in range(N):
            seg = vs[starts[i]:ends[i]]; seg = seg[~np.isnan(seg)]
            if seg.size: out[i] = np.percentile(seg, q)
        return out

    dem = arr("dem")
    hand = arr("hand"); handM = arr("hand_major"); sink = arr("sink"); slope = arr("slope")
    cn = arr("cn", 0).astype(np.float32); cn[cn <= 0] = np.nan
    wc = arr("wc", 0)
    res = {
        "elev": zmean(dem), "hand": zmean(hand), "hand_p10": zpct(hand, 10), "handM_p10": zpct(handM, 10),
        "slope": zmean(np.clip(slope, 0, 60)), "cn": zmean(cn),
        # sink storage as mm over hex (cap DSM noise at 1.5 m per cell)
        "sink_mm": np.bincount(z, weights=np.nan_to_num(np.clip(sink[m], 0, 1.5)), minlength=N) / np.maximum(cnt, 1) * 1000,
        "f_low": zmean((hand < 2).astype(float)),
        "f_crop": zmean((wc == 40).astype(float)), "f_built": zmean((wc == 50).astype(float)),
        "f_water": zmean(((wc == 80) | (wc == 90)).astype(float)),
    }
    del sink, slope, cn, wc

    # downstream hex: from the max-accumulation cell of each hex follow D8 until leaving the hex
    facc = arr("facc"); fdir = arr("fdir", 0).astype(np.int32)
    fa = np.where(m, np.nan_to_num(facc, nan=-1), -1).ravel()
    order = np.lexsort((fa, hid.ravel()))
    hz = hid.ravel()[order]
    last = np.searchsorted(hz, np.arange(N), "right") - 1
    outlet = order[last]
    res["facc_km2"] = fa[outlet] * CELL * CELL / 1e6
    D = {1: (0, 1), 2: (1, 1), 4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1), 64: (-1, 0), 128: (-1, 1)}
    dr = np.zeros(256, int); dc = np.zeros(256, int)
    for k, (a, b) in D.items(): dr[k], dc[k] = a, b
    r, c = np.divmod(outlet, ncols)
    down = np.full(N, -1, np.int32); alive = np.ones(N, bool)
    for _ in range(200):
        d = fdir[r, c]; stop = (d == 0) | ~np.isin(d, list(D))
        alive &= ~stop
        r2 = r + dr[d]; c2 = c + dc[d]
        inside = (r2 >= 0) & (r2 < nrows) & (c2 >= 0) & (c2 < ncols)
        alive &= inside
        r = np.where(alive, r2, r); c = np.where(alive, c2, c)
        h2 = hid[r, c]
        leave = alive & (h2 != np.arange(N))
        down[leave] = np.where(h2[leave] >= 0, h2[leave], -1)
        alive &= ~leave
        if not alive.any(): break
    res["down"] = down

    # ---------- 7. export web files ----------
    _log("7/8 export hex / params / boundaries", log)
    feats = []; cx = np.zeros(N); cy = np.zeros(N); amph = [""] * N
    with arcpy.da.SearchCursor(hexfc, ["hid", "amphoe", "SHAPE@"], spatial_reference=WGS) as cur:
        for i, a, g in cur:
            ring = [[round(pt.X, 5), round(pt.Y, 5)] for pt in g.getPart(0) if pt]
            feats.append({"type": "Feature", "properties": {"i": i}, "geometry": {"type": "Polygon", "coordinates": [ring]}})
            cx[i], cy[i] = g.centroid.X, g.centroid.Y; amph[i] = a
    feats.sort(key=lambda f: f["properties"]["i"])
    json.dump({"type": "FeatureCollection", "features": feats}, open(os.path.join(out_static, "hex.geojson"), "w"),
              separators=(",", ":"))
    alist = sorted(set(amph))
    params = {"n": N, "hex_km2": hex_km2, "amphoe_list": alist,
              "amph": [alist.index(a) for a in amph],
              "lon": np.round(cx, 5).tolist(), "lat": np.round(cy, 5).tolist()}
    for k, v in res.items():
        v = np.asarray(v, float)
        params[k] = np.where(np.isnan(v), -1, np.round(v, 3)).tolist() if k != "down" else v.astype(int).tolist()
    json.dump(params, open(os.path.join(out_static, "params.json"), "w"), separators=(",", ":"))

    def fc_to_geojson(fc, fields, out, tol=None):
        g = []
        with arcpy.da.SearchCursor(fc, fields + ["SHAPE@"], spatial_reference=WGS) as cur:
            for row in cur:
                shp = row[-1]
                if tol: shp = shp.generalize(tol)
                gj = json.loads(shp.JSON)
                rings = [[[round(pt[0], 5), round(pt[1], 5)] for pt in rr] for rr in gj["rings"]]
                g.append({"type": "Feature", "properties": dict(zip(fields, row[:-1])),
                          "geometry": {"type": "Polygon" if len(rings) == 1 else "MultiPolygon",
                                       "coordinates": rings if len(rings) == 1 else [[rr] for rr in rings]}})
        json.dump({"type": "FeatureCollection", "features": g}, open(out, "w", encoding="utf8"),
                  ensure_ascii=False, separators=(",", ":"))

    fc_to_geojson(os.path.join(gdb, "prov_wgs"), ["shapeName"], os.path.join(out_static, "province.geojson"), 0.0005)
    with arcpy.da.UpdateCursor(os.path.join(gdb, "amphoe_wgs"), ["shapeName"]) as cur:
        for (nm,) in cur:
            cur.updateRow([AMPHOE_TH.get(nm, nm)])
    fc_to_geojson(os.path.join(gdb, "amphoe_wgs"), ["shapeName"], os.path.join(out_static, "districts.geojson"), 0.0005)

    # ---------- 8. susceptibility PNG (HAND classes) + station reference ----------
    _log("8/8 susceptibility overlay + station reference", log)
    export_susceptibility(p("hand"), os.path.join(gdb, "prov"), out_static, ws)
    build_station_ref(dem_path, p("fill"), out_static)
    _log(f"done: {N} hexes -> {out_static}", log)
    return out_static


def export_susceptibility(hand_path, prov_fc, out_static, ws):
    """HAND -> 5 classes -> Web Mercator PNG for Leaflet imageOverlay."""
    from PIL import Image
    for _e in ("extent", "snapRaster", "cellSize"):
        arcpy.ClearEnvironment(_e)
    masked = ExtractByMask(hand_path, prov_fc); masked.save(os.path.join(ws, "hand_prov.tif"))
    wm = os.path.join(ws, "hand_wm.tif")
    arcpy.management.ProjectRaster(os.path.join(ws, "hand_prov.tif"), wm, WEBM, "BILINEAR", "60 60")
    r = arcpy.Raster(wm)
    a = arcpy.RasterToNumPyArray(wm, nodata_to_value=np.nan)
    cols = [(8, 48, 107, 190), (33, 113, 181, 170), (107, 174, 214, 150), (198, 219, 239, 120)]
    rgba = np.zeros(a.shape + (4,), np.uint8)
    for (lo, hi), col in zip([(-1, 1), (1, 2), (2, 3), (3, 5)], cols):
        rgba[(a >= lo) & (a < hi)] = col
    Image.fromarray(rgba, "RGBA").save(os.path.join(out_static, "susceptibility.png"), optimize=True)
    e = r.extent
    sw = arcpy.PointGeometry(arcpy.Point(e.XMin, e.YMin), WEBM).projectAs(WGS).firstPoint
    ne = arcpy.PointGeometry(arcpy.Point(e.XMax, e.YMax), WEBM).projectAs(WGS).firstPoint
    json.dump({"bounds": [[sw.Y, sw.X], [ne.Y, ne.X]],
               "classes": ["HAND < 1 ม.", "1–2 ม.", "2–3 ม.", "3–5 ม."],
               "colors": ["#08306b", "#2171b5", "#6baed6", "#c6dbef"]},
              open(os.path.join(out_static, "susceptibility.json"), "w", encoding="utf8"), ensure_ascii=False)


def build_station_ref(dem_path, fill_path, out_static, radius_m=300):
    """ระดับอ้างอิงผิวน้ำใน DEM (z_ref) ของสถานีวัดระดับน้ำ ThaiWater รอบจังหวัด
    z_ref = ค่าต่ำสุดของ filled DEM ในรัศมี 300 ม. — ใช้เทียบกับ HAND ของแม่น้ำสายหลัก"""
    url = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel"
    data = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                                             timeout=60).read().decode("utf8"))["data"]
    ras = arcpy.Raster(fill_path); e = ras.extent
    a = arcpy.RasterToNumPyArray(fill_path, nodata_to_value=np.nan)
    ref = {}
    for d in data:
        st = d["station"]
        pt = arcpy.PointGeometry(arcpy.Point(st["tele_station_long"], st["tele_station_lat"]), WGS).projectAs(UTM).firstPoint
        if not (e.XMin < pt.X < e.XMax and e.YMin < pt.Y < e.YMax):
            continue
        c = int((pt.X - e.XMin) / CELL); r = int((e.YMax - pt.Y) / CELL); k = int(radius_m / CELL)
        win = a[max(r - k, 0):r + k + 1, max(c - k, 0):c + k + 1]
        if win.size and not np.all(np.isnan(win)):
            ref[st["tele_station_oldcode"]] = {"z_ref": round(float(np.nanmin(win)), 2),
                                               "name": st["tele_station_name"]["th"],
                                               "lat": st["tele_station_lat"], "lon": st["tele_station_long"]}
    json.dump(ref, open(os.path.join(out_static, "stations_ref.json"), "w", encoding="utf8"),
              ensure_ascii=False, indent=1)
    return ref
