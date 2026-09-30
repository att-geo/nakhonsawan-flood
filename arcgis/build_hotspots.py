# -*- coding: utf-8 -*-
"""
เตรียมโดเมนแบบจำลอง 2 มิติ (rain-on-grid) สำหรับจุดวิกฤต + export ข้อมูลเข้า HEC-RAS 2D

build_hotspots(): กริด Web Mercator (ขนาด cell ~120 ม. บนพื้นดิน) ต่อโดเมน
    dem (FABDEM ปรับแต่งแล้ว), n (Manning จาก WorldCover), cn, hex (id ของ hex), river (index สถานี/-1), valid
    -> data/static/hotspots/<id>.npz + hotspots.json (bounds สำหรับ overlay บนเว็บ)
export_hecras(): ต่อโดเมน -> hecras/<id>/terrain.tif (UTM 30 ม.), landcover.tif + manning.csv,
    perimeter.shp (ขอบ 2D flow area), bc_lines.shp (แนวขอบไหลออก/ระดับน้ำ), README_HECRAS.md
    (ฝนรายชั่วโมง + ระดับน้ำสถานีเขียนโดย pipeline/run_hotspots.py -> hecras/<id>/rain.csv, stage_<code>.csv)
"""
import os, json, math, time
import numpy as np
import arcpy
from arcpy.sa import ExtractByMask, Reclassify, RemapValue, Con, Raster

UTM = arcpy.SpatialReference(32647); WGS = arcpy.SpatialReference(4326); WEBM = arcpy.SpatialReference(3857)
HOTSPOTS = {
    "latyao": {"name": "ลาดยาว–แม่วงก์ (สะแกกรัง)", "bbox": (99.45, 15.68, 99.85, 15.98), "stations": ["Ct.5A", "Ct.4", "SKG007"]},
    "mueang": {"name": "เมืองนครสวรรค์ (ปากน้ำโพ)", "bbox": (99.98, 15.60, 100.22, 15.82), "stations": ["C.2", "CPY001", "PIN005"]},
    "chumsaeng": {"name": "ชุมแสง (น่าน–ยม)", "bbox": (100.15, 15.80, 100.42, 16.00), "stations": ["N.67", "NAN008"]},
    # ท่าตะโกทั้งอำเภอ + ต.พระนอน (เมือง) + ต.ทับกฤช (ชุมแสง) — พื้นที่ท่วมขังซ้ำซาก/นาน
    # เป็นแอ่งรับน้ำ: พื้นที่รับน้ำที่ไหลลง AOI ~3,740 กม² (ไพศาลี หนองบัว ชุมแสง พยุหะคีรี ตากฟ้า) จึงต้องคลุมทั้งพื้นที่รับน้ำ
    # bbox = พื้นที่รับน้ำ (Watershed จาก fdir, pour point = cell ใน AOI ที่ไม่ใช่แม่น้ำสายหลัก) + แม่น้ำน่านชุมแสง–ปากน้ำโพ
    # cell 180 ม. เพื่อให้รันใน GitHub Actions ได้ ; long_h = รันต่อหลังพยากรณ์ (ไม่มีฝน) เพื่อหาเวลาที่น้ำขังจะลด
    "thatako": {"name": "ท่าตะโก + พระนอน/ทับกฤช (แอ่งรับน้ำ)", "bbox": (100.10, 15.33, 100.86, 15.99),
                "stations": ["N.67", "NAN008", "C.2", "CPY001"], "cell": 180.0, "long_h": 336,
                "aoi": "thatako_aoi.geojson", "contrib": "thatako_contrib.geojson"},
    # โดเมนสำหรับจำลองเหตุการณ์จริงย้อนหลัง (ไม่รันรายชั่วโมง -> เขียนลง hotspots_events.json) : ขยายขึ้นเหนือถึงสถานี Y.5 โพทะเล
    # เพื่อให้มีน้ำบ่าจากพิจิตรผ่านชุมแสง ; นอกเขตจังหวัดใช้ FABDEM ดิบ + WorldCover/CN ภูมิภาค ; cell 300 ม. เพื่อจำลอง 3–4 เดือนได้
    "thatako_ev": {"name": "แอ่งท่าตะโก–ชุมแสง–โพทะเล (เหตุการณ์ย้อนหลัง)", "bbox": (100.10, 15.33, 100.86, 16.12),
                   "stations": ["Y.5", "N.67", "NAN008", "C.2", "CPY001"], "cell": 300.0, "event_only": True, "fill_outside": True,
                   "extra_stations": {"Y.5": {"name": "หน้าอำเภอโพทะเล", "lat": 16.09481, "lon": 100.259903}},
                   "aoi": "thatako_aoi.geojson", "contrib": "thatako_contrib.geojson"},
}
MANNING = {10: 0.10, 20: 0.08, 30: 0.05, 40: 0.06, 50: 0.08, 60: 0.035, 70: 0.03, 80: 0.03, 90: 0.07, 95: 0.10, 100: 0.05}
CELL_GROUND = 120.0


def _log(m, log=None):
    (log or print)(time.strftime("%H:%M:%S ") + str(m))


def build_hotspots(project_dir, repo_dir, cell_ground=CELL_GROUND, log=None, only=None):
    arcpy.CheckOutExtension("Spatial"); arcpy.env.overwriteOutput = True; arcpy.env.pyramid = "NONE"
    ws30 = os.path.join(project_dir, "static_rasters"); q = lambda n: os.path.join(ws30, n + ".tif")
    wsh = os.path.join(project_dir, "hotspot_rasters"); os.makedirs(wsh, exist_ok=True)
    out = os.path.join(repo_dir, "data", "static", "hotspots"); os.makedirs(out, exist_ok=True)
    refs = json.load(open(os.path.join(repo_dir, "data", "static", "stations_ref.json"), encoding="utf8"))
    index = {}; index_ev = {}
    for hid_, cfg in HOTSPOTS.items():
        if only and hid_ not in only:
            continue
        _log(f"H {hid_}: grid", log)
        w, s, e, n = cfg["bbox"]
        cell_g = float(cfg.get("cell", cell_ground))
        sw = arcpy.PointGeometry(arcpy.Point(w, s), WGS).projectAs(WEBM).firstPoint
        ne = arcpy.PointGeometry(arcpy.Point(e, n), WGS).projectAs(WEBM).firstPoint
        lat0 = 0.5 * (s + n)
        cell = cell_g / math.cos(math.radians(lat0))                       # ขนาด cell ใน Web Mercator
        nx = int((ne.X - sw.X) // cell); ny = int((ne.Y - sw.Y) // cell)
        ext = arcpy.Extent(sw.X, sw.Y, sw.X + nx * cell, sw.Y + ny * cell)
        for e_ in ("extent", "snapRaster", "cellSize", "mask"):
            arcpy.ClearEnvironment(e_)
        arcpy.env.extent = ext; arcpy.env.outputCoordinateSystem = WEBM
        P = lambda nm: os.path.join(wsh, f"{hid_}_{nm}.tif")
        from arcpy.sa import CreateConstantRaster
        CreateConstantRaster(0, "INTEGER", cell, ext).save(P("tmpl"))          # แม่แบบกริดให้ทุกชั้นตรงกัน
        arcpy.env.snapRaster = P("tmpl"); arcpy.env.cellSize = P("tmpl")
        arcpy.management.ProjectRaster(q("dem"), P("dem"), WEBM, "BILINEAR", f"{cell} {cell}")
        arcpy.management.ProjectRaster(q("wc"), P("wc"), WEBM, "NEAREST", f"{cell} {cell}")
        arcpy.management.ProjectRaster(q("cn"), P("cn"), WEBM, "NEAREST", f"{cell} {cell}")
        arcpy.management.ProjectRaster(q("hexid"), P("hex"), WEBM, "NEAREST", f"{cell} {cell}")
        maj = q("major_w") if arcpy.Exists(q("major_w")) else q("major")
        ll = arcpy.Point(ext.XMin, ext.YMin)
        A = lambda nm, nod: arcpy.RasterToNumPyArray(P(nm), ll, nx, ny, nod)
        dem = A("dem", np.nan).astype(np.float32); wc = A("wc", 0); cn = A("cn", 0); hx = A("hex", -1).astype(np.int32)
        if cfg.get("fill_outside"):                       # นอกเขตจังหวัด (+3 กม.) -> FABDEM ดิบ / WorldCover, CN ภูมิภาค
            ur = os.path.join(project_dir, "upstream_rasters")
            arcpy.management.ProjectRaster(q("fab_wgs"), P("fab"), WEBM, "BILINEAR", f"{cell} {cell}")
            fab = A("fab", np.nan).astype(np.float32)
            dem = np.where(np.isnan(dem), fab, dem)
            for nm_, src_, arr_ in (("wcr", "wc_reg", "wc"), ("cnr", "cn_reg", "cn")):
                sp_ = os.path.join(ur, src_ + ".tif")
                if arcpy.Exists(sp_):
                    arcpy.management.ProjectRaster(sp_, P(nm_), WEBM, "NEAREST", f"{cell} {cell}")
                    r_ = A(nm_, 0)
                    if arr_ == "wc":
                        wc = np.where(wc > 0, wc, r_)
                    else:
                        cn = np.where(cn > 0, cn, r_)
        # กริดย่อย ~30 ม. (f×f ต่อ cell): ท้องน้ำ = ค่าต่ำสุดของ DEM 30 ม. ใน cell (ระดับอ้างอิงของ cell แม่น้ำ)
        # และ cell แม่น้ำ = มีเส้นแม่น้ำสายหลักผ่าน cell (ค่าสูงสุดของกริดย่อย) — ถ้า ProjectRaster NEAREST ตรงเป็น cell ใหญ่
        # เส้นแม่น้ำกว้าง 1 cell (30 ม.) จะขาดเป็นท่อน ๆ (เดิม ~70% ของ cell แม่น้ำหายและแตกเป็นหลายสิบชิ้น)
        f = max(4, int(round(cell_g / 30.0))); sub = cell / f
        CreateConstantRaster(0, "INTEGER", sub, ext).save(P("tmpl4"))
        arcpy.env.snapRaster = P("tmpl4"); arcpy.env.cellSize = P("tmpl4")
        arcpy.management.ProjectRaster(q("dem"), P("dem4"), WEBM, "NEAREST", f"{sub} {sub}")
        arcpy.management.ProjectRaster(maj, P("major4"), WEBM, "NEAREST", f"{sub} {sub}")
        a4 = arcpy.RasterToNumPyArray(P("dem4"), ll, nx * f, ny * f, np.nan)
        zbed = np.nanmin(a4.reshape(ny, f, nx, f), axis=(1, 3)).astype(np.float32)
        m4 = arcpy.RasterToNumPyArray(P("major4"), ll, nx * f, ny * f, 0)
        mj = (m4.reshape(ny, f, nx, f) > 0).any(axis=(1, 3)).astype(np.uint8)
        zbed = np.where(np.isnan(zbed), dem, zbed)
        arcpy.env.snapRaster = P("tmpl"); arcpy.env.cellSize = P("tmpl")
        valid = ~np.isnan(dem)
        nman = np.full(dem.shape, 0.06, np.float32)
        for k, v in MANNING.items():
            nman[wc == k] = v
        # river cells -> nearest station of this hotspot
        river = np.full(dem.shape, -1, np.int16)
        refs_ = dict(refs)
        for c_, e_ in (cfg.get("extra_stations") or {}).items():   # สถานีนอกจังหวัด: z_ref = ค่าต่ำสุดของ DEM รัศมี ~1 cell
            if c_ in refs_:
                continue
            X_ = e_["lon"] * math.pi / 180 * 6378137.0; Y_ = math.log(math.tan(math.pi / 4 + math.radians(e_["lat"]) / 2)) * 6378137.0
            ci_ = int((X_ - ext.XMin) // cell); ri_ = int((ext.YMax - Y_) // cell)
            win = dem[max(ri_ - 1, 0):ri_ + 2, max(ci_ - 1, 0):ci_ + 2]
            refs_[c_] = {"name": e_["name"], "lat": e_["lat"], "lon": e_["lon"], "z_ref": round(float(np.nanmin(win)), 2)}
        st = [refs_[c] | {"code": c} for c in cfg["stations"] if c in refs_]
        rr, cc = np.where((mj > 0) & valid)
        if rr.size and st:
            X = ext.XMin + (cc + 0.5) * cell; Y = ext.YMax - (rr + 0.5) * cell
            lon = X / 6378137.0 * 180 / math.pi; lat = np.degrees(2 * np.arctan(np.exp(Y / 6378137.0)) - math.pi / 2)
            d = np.array([np.hypot((lon - s_["lon"]) * 107.1, (lat - s_["lat"]) * 110.6) for s_ in st])
            river[rr, cc] = d.argmin(0)
        # พื้นที่ศึกษา (ตำบล) -> index ราย cell (-1 = นอก AOI) สำหรับสรุปผลรายตำบล
        aoi = None; aoi_meta = None
        if cfg.get("aoi"):
            gj = os.path.join(out, cfg["aoi"])
            fc = os.path.join(wsh, f"{hid_}_aoipoly.shp")
            arcpy.conversion.JSONToFeatures(gj, fc, "POLYGON")
            flds = [f.name for f in arcpy.ListFields(fc)]
            code_f = next(f for f in flds if f.lower() == "code")
            codes = sorted({r[0] for r in arcpy.da.SearchCursor(fc, [code_f])})
            if "aidx" not in flds:
                arcpy.management.AddField(fc, "aidx", "SHORT")
            with arcpy.da.UpdateCursor(fc, [code_f, "aidx"]) as uc:
                for r in uc:
                    r[1] = codes.index(r[0]); uc.updateRow(r)
            arcpy.conversion.PolygonToRaster(fc, "aidx", P("aoigrid"), "CELL_CENTER", cellsize=cell)
            aoi = A("aoigrid", -1).astype(np.int16)
            props = {f["properties"]["code"]: f["properties"] for f in json.load(open(gj, encoding="utf8"))["features"]}
            aoi_meta = [{"code": c, "name": props[c]["name"], "district": props[c]["district"],
                         "area_km2": props[c]["area_km2"], "in_existing": bool(props[c].get("in_existing"))} for c in codes]
        meta = {"id": hid_, "name": cfg["name"], "dx": cell_g, "cell_webm": cell, "shape": [ny, nx],
                "bounds": [[s, w], [n, e]], "extent_webm": [ext.XMin, ext.YMin, ext.XMax, ext.YMax],
                "stations": [{"code": s_["code"], "name": s_["name"], "z_ref": s_["z_ref"], "lat": s_["lat"], "lon": s_["lon"]} for s_ in st]}
        # exact lat/lon bounds of the Web Mercator grid (for Leaflet imageOverlay)
        swg = arcpy.PointGeometry(arcpy.Point(ext.XMin, ext.YMin), WEBM).projectAs(WGS).firstPoint
        neg = arcpy.PointGeometry(arcpy.Point(ext.XMax, ext.YMax), WEBM).projectAs(WGS).firstPoint
        meta["bounds"] = [[swg.Y, swg.X], [neg.Y, neg.X]]
        extra = {}
        if aoi is not None:
            extra["aoi"] = aoi; meta["aoi"] = aoi_meta
        for k_ in ("long_h", "contrib"):
            if cfg.get(k_):
                meta[k_] = cfg[k_]
        np.savez_compressed(os.path.join(out, f"{hid_}.npz"), dem=np.nan_to_num(dem, nan=0), n=nman, crop=(wc == 40),
                            zbed=np.nan_to_num(zbed, nan=0),
                            cn=np.where(cn > 0, cn, 80).astype(np.uint8), hex=hx, river=river, valid=valid,
                            meta=json.dumps(meta, ensure_ascii=False), **extra)
        (index_ev if cfg.get("event_only") else index)[hid_] = meta
        _log(f"   {ny}x{nx} cells, river cells {int((river >= 0).sum())}", log)
        arcpy.ClearEnvironment("outputCoordinateSystem")
    arcpy.ResetEnvironments(); arcpy.env.overwriteOutput = True
    for fn_, ix_ in (("hotspots.json", index), ("hotspots_events.json", index_ev)):
        if not ix_:
            continue
        idx_path = os.path.join(out, fn_)
        old = json.load(open(idx_path, encoding="utf8")) if os.path.exists(idx_path) else {}
        old.update(ix_)
        json.dump(old, open(idx_path, "w", encoding="utf8"), ensure_ascii=False, indent=1)
    index.update(index_ev)
    return index


def export_hecras(project_dir, repo_dir, log=None, only=None):
    """เตรียมข้อมูลสำหรับสร้าง 2D Flow Area ใน HEC-RAS 6.x (RAS Mapper)"""
    arcpy.ResetEnvironments()
    arcpy.CheckOutExtension("Spatial"); arcpy.env.overwriteOutput = True
    ws30 = os.path.join(project_dir, "static_rasters"); q = lambda n: os.path.join(ws30, n + ".tif")
    base = os.path.join(repo_dir, "hecras"); os.makedirs(base, exist_ok=True)
    for hid_, cfg in HOTSPOTS.items():
        if only and hid_ not in only:
            continue
        d = os.path.join(base, hid_); os.makedirs(d, exist_ok=True)
        w, s, e, n = cfg["bbox"]
        poly = arcpy.Polygon(arcpy.Array([arcpy.Point(w, s), arcpy.Point(e, s), arcpy.Point(e, n), arcpy.Point(w, n)]), WGS).projectAs(UTM)
        per = os.path.join(d, "perimeter.shp")
        arcpy.management.CopyFeatures([poly], per)
        for e_ in ("extent", "snapRaster", "cellSize", "mask"):
            arcpy.ClearEnvironment(e_)
        rect = f"{poly.extent.XMin} {poly.extent.YMin} {poly.extent.XMax} {poly.extent.YMax}"
        arcpy.management.Clip(q("dem"), rect, os.path.join(d, "terrain.tif"))
        arcpy.management.Clip(q("wc"), rect, os.path.join(d, "landcover.tif"))
        with open(os.path.join(d, "manning.csv"), "w", encoding="utf-8") as f:
            f.write("worldcover_class,name,mannings_n\n")
            names = {10: "Tree cover", 20: "Shrubland", 30: "Grassland", 40: "Cropland (paddy)", 50: "Built-up", 60: "Bare",
                     70: "Snow", 80: "Water", 90: "Herbaceous wetland", 95: "Mangroves", 100: "Moss"}
            for k, v in MANNING.items():
                f.write(f"{k},{names[k]},{v}\n")
        # boundary lines: 4 sides of the perimeter (normal depth outflow)
        b = poly.extent
        sides = {"north": [(b.XMin, b.YMax), (b.XMax, b.YMax)], "south": [(b.XMin, b.YMin), (b.XMax, b.YMin)],
                 "west": [(b.XMin, b.YMin), (b.XMin, b.YMax)], "east": [(b.XMax, b.YMin), (b.XMax, b.YMax)]}
        bc = os.path.join(d, "bc_lines.shp")
        arcpy.management.CreateFeatureclass(d, "bc_lines.shp", "POLYLINE", spatial_reference=UTM)
        arcpy.management.AddField(bc, "side", "TEXT", field_length=8)
        with arcpy.da.InsertCursor(bc, ["SHAPE@", "side"]) as ic:
            for k, pts in sides.items():
                ic.insertRow([arcpy.Polyline(arcpy.Array([arcpy.Point(*p) for p in pts]), UTM), k])
        open(os.path.join(d, "README_HECRAS.md"), "w", encoding="utf-8").write(f"""# HEC-RAS 2D — {cfg['name']}

ไฟล์ในโฟลเดอร์นี้สร้างโดย ArcGIS Pro (arcgis/build_hotspots.py) สำหรับทำแบบจำลอง 2D rain-on-grid เทียบกับโมเดลในเว็บ

1. HEC-RAS 6.x → New Project → RAS Mapper → Projection: `WGS 84 / UTM zone 47N (EPSG:32647)`
2. Terrain → Create New Terrain → `terrain.tif` (FABDEM 30 ม. + burn ลำน้ำ/ยกคันกั้นน้ำจาก OSM)
3. Map Layers → Land Cover → `landcover.tif` แล้วกรอกค่า Manning's n จาก `manning.csv`
4. Geometry → 2D Flow Areas → Import `perimeter.shp` → cell 60–120 ม. + Refinement ตามแนวแม่น้ำ/ถนน
5. Boundary Condition Lines → import `bc_lines.shp` → Normal Depth (slope 0.001)
   และเพิ่ม Stage Hydrograph ที่ลำน้ำหลักจาก `stage_<สถานี>.csv`
6. Unsteady Flow → Meteorological Data → Precipitation = Point/Constant → `rain.csv` (มม./ชม. เฉลี่ยทั้งโดเมน)
   หรือ Gridded จาก Open-Meteo, Infiltration = SCS Curve Number (ใช้ raster cn จาก ArcGIS Pro)
7. Compute (Full Momentum / Diffusion Wave) แล้วเทียบ max depth กับ `data/live/hotspots/{hid_}_max72.png`

`rain.csv`, `stage_*.csv` ถูกอัปเดตทุกครั้งที่รัน `python pipeline/run_hotspots.py --site . --hecras`
""")
        if cfg.get("aoi"):
            # ขอบเขตตำบลที่ศึกษา + พื้นที่รับน้ำ (ใช้เป็น perimeter ทางเลือกที่เล็กกว่า bbox) เป็น UTM 47N
            hs = os.path.join(repo_dir, "data", "static", "hotspots")
            for src, dst in ((cfg["aoi"], "aoi_tambon.shp"), (cfg.get("contrib"), "contributing_area.shp")):
                if not src:
                    continue
                tmp = os.path.join(arcpy.env.scratchGDB, f"{hid_}_{dst[:-4]}")
                arcpy.conversion.JSONToFeatures(os.path.join(hs, src), tmp, "POLYGON")
                arcpy.management.Project(tmp, os.path.join(d, dst), UTM)
            with open(os.path.join(d, "README_HECRAS.md"), "a", encoding="utf-8") as f:
                f.write(f"""
## โดเมนแอ่งรับน้ำท่าตะโก

- `perimeter.shp` = กรอบทั้งพื้นที่รับน้ำ (~{(e - w) * 107:.0f} × {(n - s) * 110.6:.0f} กม.) — ท่าตะโกรับน้ำจากไพศาลี หนองบัว ชุมแสง พยุหะคีรี ตากฟ้า
  ถ้าย่อโดเมน ให้ใช้ `contributing_area.shp` + buffer ≥ 3 กม. และ**ต้องครอบแม่น้ำน่านช่วงชุมแสง–ปากน้ำโพ** (ทางระบายหลักของบึงบอระเพ็ด/ท่าตะโก)
- `aoi_tambon.shp` = ตำบลที่ศึกษา (ท่าตะโก 10 ตำบล + พระนอน + ทับกฤช + ไผ่สิงห์) → RAS Mapper: Results → Calculate Profile/Zonal stats
- ใส่ Stage Hydrograph ที่แม่น้ำน่าน `stage_N67.csv` (ต้นน้ำ, ชุมแสง) และ `stage_C2.csv` (ท้ายน้ำ, ปากน้ำโพ)
- ระยะเวลาท่วมขัง: Unsteady → Simulation Time ≥ 14 วัน, RAS Mapper → Duration (depth ≥ 0.1 ม.) และ Arrival/Recession Time
  เทียบกับ `data/live/hotspots/{hid_}_dur.png` และตาราง `ponding` ใน `hotspots_live.json`
""")
        _log(f"HEC-RAS inputs -> {d}", log)
