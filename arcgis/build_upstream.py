# -*- coding: utf-8 -*-
"""
Nakhon Sawan Flood Watch — upstream inflow analysis (ArcGIS Pro 3.x + Spatial Analyst)

หาพื้นที่ใน 4 จังหวัดต้นน้ำ (กำแพงเพชร พิจิตร พิษณุโลก เพชรบูรณ์) ที่ไหลเข้าจังหวัดนครสวรรค์
  1) DEM ภูมิภาค 90 ม. (Copernicus GLO-30) -> Fill / Flow Direction / Flow Accumulation
  2) ทุก cell ใน 4 จังหวัด: เดินตามทิศการไหล (pointer jumping) หา "จุดที่น้ำเข้า นว." (entry hex)
     และระยะทางไหล -> เวลาเดินทางของน้ำ
  3) แบ่งเป็น zone = (entry hex, จังหวัด, จุด grid ฝน 0.2°, ช่วงเวลาเดินทาง 6 ชม.)
     พร้อมพื้นที่, Curve Number, lag -> ใช้ใน pipeline คำนวณ hydrograph ที่จุดเข้า
  4) คำนวณ HAND ของแม่น้ำสายหลักใน นว. ใหม่ (30 ม.) โดยนับพื้นที่รับน้ำจากต้นน้ำด้วย (weighted flow accumulation)
     + hypsometry ราย hex (HAND P5/P10/P25/P50/P75) สำหรับเติมน้ำล้นตลิ่งแบบ level-pool
ผลลัพธ์: data/static/upstream_zones.json, upstream_entries.json, upstream_basins.geojson, params.json (อัปเดต)
"""
import os, json, time, urllib.request
import numpy as np
import arcpy
from arcpy.sa import (Fill, FlowDirection, FlowAccumulation, FlowDistance, Con, Raster, IsNull,
                      Reclassify, RemapValue, ExtractByMask, SnapPourPoint)

UTM = arcpy.SpatialReference(32647)
WGS = arcpy.SpatialReference(4326)
PROV = {"Nakhon Sawan": ("นครสวรรค์", 60), "Kamphaeng Phet": ("กำแพงเพชร", 62), "Phitsanulok": ("พิษณุโลก", 65),
        "Phichit": ("พิจิตร", 66), "Phetchabun": ("เพชรบูรณ์", 67)}
UPCODES = (62, 65, 66, 67)
GRID0, GSTEP, GCOLS = (99.0, 15.0), 0.2, 16          # จุดฝน Open-Meteo: lon=99.0+0.2i, lat=15.0+0.2j
# CN ระดับภูมิภาค (ค่ากลาง HSG C/D)
WC_CN = {10: 72, 20: 79, 30: 76, 40: 84, 50: 91, 60: 87, 70: 98, 80: 98, 90: 86, 95: 82, 100: 82}
D8 = {1: (0, 1), 2: (1, 1), 4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1), 64: (-1, 0), 128: (-1, 1)}
# จุดอ้างอิงชื่อแม่น้ำของจุดน้ำเข้าหลัก (ใกล้สุดภายใน 25 กม.)
RIVER_HINT = [("P.17", "แม่น้ำปิง", 16.05, 99.87), ("N.67", "แม่น้ำน่าน", 15.935, 100.32),
              ("Y.5", "แม่น้ำยม", 15.921, 100.21), ("Ct.5A", "แม่น้ำแม่วงก์", 15.928, 99.502)]


def _log(m, log=None):
    (log or print)(time.strftime("%H:%M:%S ") + str(m))


def _download(src, log):
    for la in (15, 16, 17):
        for lo in (99, 100, 101):
            fn = f"cop_N{la}E{lo:03d}.tif"; p = os.path.join(src, fn)
            if not os.path.exists(p):
                n = f"Copernicus_DSM_COG_10_N{la}_00_E{lo:03d}_00_DEM"
                _log(f"download {fn}", log)
                urllib.request.urlretrieve(f"https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif", p + ".part")
                os.replace(p + ".part", p)


def build_upstream(project_dir, repo_dir, cell=90.0, v_ms=0.7, lag_bin_h=6, major_km2=1000.0, log=None):
    arcpy.CheckOutExtension("Spatial"); arcpy.env.overwriteOutput = True
    arcpy.env.pyramid = "NONE"                           # ไม่สร้าง pyramids ให้ raster กลางทาง (เร็วขึ้นมาก)
    src = os.path.join(project_dir, "source"); gdb = os.path.join(project_dir, "flood_static.gdb")
    ws = os.path.join(project_dir, "upstream_rasters"); os.makedirs(ws, exist_ok=True)
    ws30 = os.path.join(project_dir, "static_rasters")
    out_static = os.path.join(repo_dir, "data", "static")
    p = lambda n: os.path.join(ws, n + ".tif")
    for e in ("extent", "snapRaster", "cellSize", "mask"):
        arcpy.ClearEnvironment(e)
    _download(src, log)

    # ---------------- 1. boundaries (5 provinces) ----------------
    _log("U1 boundaries", log)
    gj = json.load(open(os.path.join(src, "tha_adm1.geojson"), encoding="utf8"))
    feats = []
    for f in gj["features"]:
        for k, (th, code) in PROV.items():
            if f["properties"]["shapeName"].startswith(k):
                f["properties"] = {"pcode": code, "pname": th}; feats.append(f)
                break
    jr = os.path.join(src, "region5.geojson")
    json.dump({"type": "FeatureCollection", "features": feats}, open(jr, "w", encoding="utf8"), ensure_ascii=False)
    reg_wgs = os.path.join(gdb, "region_wgs"); reg = os.path.join(gdb, "region"); reg_buf = os.path.join(gdb, "region_buf")
    arcpy.conversion.JSONToFeatures(jr, reg_wgs, "POLYGON")
    arcpy.management.Project(reg_wgs, reg, UTM)
    arcpy.analysis.PairwiseBuffer(reg, reg_buf, "2 Kilometers", "ALL")

    # ---------------- 2. regional DEM + hydrology ----------------
    _log("U2 regional DEM 90 m + Fill/FlowDir/FlowAcc", log)
    if not arcpy.Exists(p("fdir_reg")):
        tiles = [os.path.join(src, f"cop_N{la}E{lo:03d}.tif") for la in (15, 16, 17) for lo in (99, 100, 101)]
        arcpy.management.MosaicToNewRaster(tiles, ws, "dem_reg_wgs.tif", WGS, "32_BIT_FLOAT", None, 1, "MEAN")
        arcpy.management.ProjectRaster(p("dem_reg_wgs"), p("dem_reg_utm"), UTM, "BILINEAR", f"{cell} {cell}")
        ExtractByMask(p("dem_reg_utm"), reg_buf).save(p("dem_reg"))
        fill = Fill(p("dem_reg")); fill.save(p("fill_reg"))
        FlowDirection(fill, "NORMAL", None, "D8").save(p("fdir_reg"))
    arcpy.env.snapRaster = p("dem_reg"); arcpy.env.extent = p("dem_reg"); arcpy.env.cellSize = p("dem_reg")
    arcpy.conversion.PolygonToRaster(reg, "pcode", p("prov_reg"), "CELL_CENTER", "", cell)
    arcpy.conversion.PolygonToRaster(os.path.join(gdb, "hex"), "hid", p("hex_reg"), "CELL_CENTER", "", cell)

    # rain grid cells (0.2°) -> raster of grid id
    fish = os.path.join(gdb, "raingrid_wgs")
    ox, oy = GRID0[0] - GSTEP / 2, GRID0[1] - GSTEP / 2
    arcpy.management.CreateFishnet(fish, f"{ox} {oy}", f"{ox} {oy + 1}", GSTEP, GSTEP, 17, GCOLS, None, "NO_LABELS",
                                   "#", "POLYGON")
    arcpy.management.DefineProjection(fish, WGS)
    arcpy.management.AddField(fish, "gid", "LONG")
    with arcpy.da.UpdateCursor(fish, ["SHAPE@", "gid"]) as cur:
        for g, _ in cur:
            c = g.centroid
            cur.updateRow([g, int(round((c.Y - GRID0[1]) / GSTEP)) * GCOLS + int(round((c.X - GRID0[0]) / GSTEP))])
    arcpy.management.Project(fish, os.path.join(gdb, "raingrid"), UTM)
    arcpy.conversion.PolygonToRaster(os.path.join(gdb, "raingrid"), "gid", p("gid_reg"), "CELL_CENTER", "", cell)

    # land cover -> CN (90 m)
    if not arcpy.Exists(p("cn_reg")):
        # project ตรงจาก tile 10 ม. ไป 90 ม. ภายใต้ env.extent ของ DEM ภูมิภาค (เร็วกว่า clip ที่ความละเอียดเต็ม)
        arcpy.management.ProjectRaster(os.path.join(src, "wc_N15E099.tif"), p("wc_reg"), UTM, "NEAREST", f"{cell} {cell}")
        Reclassify(p("wc_reg"), "Value", RemapValue([[k, v] for k, v in WC_CN.items()]), "NODATA").save(p("cn_reg"))

    # ---------------- 3. trace every upstream cell to its NS entry ----------------
    _log("U3 tracing flow paths to Nakhon Sawan", log)
    ref = arcpy.Raster(p("dem_reg")); ll = arcpy.Point(ref.extent.XMin, ref.extent.YMin)
    nr, nc = ref.height, ref.width; xmin, ymax = ref.extent.XMin, ref.extent.YMax
    A = lambda n, nod: arcpy.RasterToNumPyArray(p(n), ll, nc, nr, nod)
    fdir = A("fdir_reg", 0).astype(np.int32).ravel()
    prov = A("prov_reg", 0).astype(np.int32).ravel()
    hexr = A("hex_reg", -1).astype(np.int32).ravel()
    gidr = A("gid_reg", -1).astype(np.int32).ravel()
    cnr = A("cn_reg", 0).astype(np.float32).ravel()
    N = nr * nc; idx = np.arange(N, dtype=np.int64)
    dr = np.zeros(256, np.int64); dc = np.zeros(256, np.int64); valid = np.zeros(256, bool)
    for k, (a, b) in D8.items():
        dr[k], dc[k], valid[k] = a, b, True
    fd = np.where((fdir > 0) & (fdir < 256), fdir, 0)
    r, c = np.divmod(idx, nc)
    r2, c2 = r + dr[fd], c + dc[fd]
    ok = valid[fd] & (r2 >= 0) & (r2 < nr) & (c2 >= 0) & (c2 < nc)
    dn = np.where(ok, r2 * nc + c2, -1)
    step = np.where(ok, np.where((dr[fd] != 0) & (dc[fd] != 0), cell * 1.41421, cell), 0.0)
    del r, c, r2, c2, ok
    up = np.isin(prov, UPCODES)
    cont = up & (dn >= 0)
    nxt = np.where(cont & up[np.maximum(dn, 0)], dn, idx)
    dist = np.where(nxt != idx, step, 0.0)
    for _ in range(24):                                   # pointer jumping: log2(path length)
        dist = dist + dist[nxt]
        nxt2 = nxt[nxt]
        if np.array_equal(nxt2, nxt):
            break
        nxt = nxt2
    term = nxt
    exit_cell = np.where(up, dn[term], -1)                # first cell after leaving the 4 provinces
    dist = dist + np.where(up, step[term], 0)
    enters = up & (exit_cell >= 0) & (prov[np.maximum(exit_cell, 0)] == 60)
    # entry hex: follow flow inside NS until a hex cell is reached
    ex_u = np.unique(exit_cell[enters])
    cur = ex_u.copy(); hx = hexr[cur]
    for _ in range(80):
        need = hx < 0
        if not need.any():
            break
        cur = np.where(need & (dn[cur] >= 0), dn[cur], cur); hx = np.where(need, hexr[cur], hx)
    ex_hex = dict(zip(ex_u.tolist(), hx.tolist()))
    cells = np.where(enters)[0]
    ehex = np.array([ex_hex[e] for e in exit_cell[cells].tolist()], np.int32)
    keep = ehex >= 0
    cells, ehex = cells[keep], ehex[keep]
    lag = dist[cells] / 1000.0 / (v_ms * 3.6)             # hours
    pc = prov[cells]; gd = gidr[cells]; cn = cnr[cells]; cn = np.where(cn > 0, cn, 80)
    cell_km2 = cell * cell / 1e6
    _log(f"   contributing cells {cells.size:,} = {cells.size * cell_km2:,.0f} km²", log)

    # ---------------- 4. zones ----------------
    lb = np.minimum((lag // lag_bin_h).astype(np.int64), 999)
    key = ((ehex.astype(np.int64) * 100 + pc) * 1000 + np.maximum(gd, 0)) * 1000 + lb
    uk, inv = np.unique(key, return_inverse=True)
    cnt = np.bincount(inv).astype(float)
    zones = {
        "n": int(uk.size), "lag_bin_h": lag_bin_h, "v_ms": v_ms, "grid": {"lon0": GRID0[0], "lat0": GRID0[1], "step": GSTEP, "cols": GCOLS},
        "entry": (uk // 100000000).astype(int).tolist(),
        "pcode": ((uk // 1000000) % 100).astype(int).tolist(),
        "gid": ((uk // 1000) % 1000).astype(int).tolist(),
        "area_km2": np.round(cnt * cell_km2, 3).tolist(),
        "cn": np.round(np.bincount(inv, weights=cn) / cnt, 1).tolist(),
        "lag_h": np.round(np.bincount(inv, weights=lag) / cnt, 1).tolist(),
    }
    json.dump(zones, open(os.path.join(out_static, "upstream_zones.json"), "w"), separators=(",", ":"))

    # entries summary
    prm = json.load(open(os.path.join(out_static, "params.json")))
    ents = {}
    ek, ekc = np.unique(ehex.astype(np.int64) * 100 + pc, return_counts=True)
    for kk, n in zip(ek.tolist(), ekc.tolist()):
        e, pcd = kk // 100, kk % 100
        d = ents.setdefault(e, {"hex": e, "area_km2": 0.0, "by_prov": {}})
        d["area_km2"] += n * cell_km2; d["by_prov"][str(pcd)] = d["by_prov"].get(str(pcd), 0) + n * cell_km2
    elist = []
    for e, d in ents.items():
        lon, lat = prm["lon"][e], prm["lat"][e]
        d["lon"], d["lat"] = lon, lat
        d["amphoe"] = prm["amphoe_list"][prm["amph"][e]]
        d["area_km2"] = round(d["area_km2"], 1)
        d["by_prov"] = {k: round(v, 1) for k, v in d["by_prov"].items()}
        d["major"] = d["area_km2"] >= major_km2
        best = min(RIVER_HINT, key=lambda h: (h[2] - lat) ** 2 + (h[3] - lon) ** 2)
        dkm = (((best[2] - lat) * 110.6) ** 2 + ((best[3] - lon) * 107.1) ** 2) ** 0.5
        d["river"] = best[1] if (d["major"] and dkm < 25) else ""
        d["gauge"] = best[0] if (d["major"] and dkm < 25) else ""
        elist.append(d)
    elist.sort(key=lambda d: -d["area_km2"])
    prov_area = {str(code): 0.0 for code in UPCODES}
    for code in UPCODES:
        prov_area[str(code)] = round(float((prov == code).sum()) * cell_km2, 0)
    json.dump({"entries": elist, "province_area_km2": prov_area,
               "province_names": {str(v[1]): v[0] for v in PROV.values()},
               "contrib_km2": {str(code): round(float((pc == code).sum()) * cell_km2, 0) for code in UPCODES}},
              open(os.path.join(out_static, "upstream_entries.json"), "w", encoding="utf8"), ensure_ascii=False, indent=0)
    _log(f"   entries {len(elist)}; major: " + ", ".join(f"{d['river'] or d['amphoe']} {d['area_km2']:.0f}" for d in elist if d['major']), log)

    # contributing-area polygons for the map
    contrib = np.zeros(N, np.int16); contrib[cells] = pc
    ras = arcpy.NumPyArrayToRaster(contrib.reshape(nr, nc), ll, cell, cell, 0); ras.save(p("contrib"))
    arcpy.management.DefineProjection(p("contrib"), UTM)
    poly = os.path.join(gdb, "upstream_poly")
    arcpy.conversion.RasterToPolygon(p("contrib"), poly, "SIMPLIFY", "Value", "MULTIPLE_OUTER_PART")
    diss = os.path.join(gdb, "upstream_basins")
    arcpy.management.Dissolve(poly, diss, "gridcode")
    g = []
    names = {v[1]: v[0] for v in PROV.values()}
    with arcpy.da.SearchCursor(diss, ["gridcode", "SHAPE@"], spatial_reference=WGS) as cur_:
        for code, shp in cur_:
            shp = shp.generalize(0.003)
            polys = []
            for part in json.loads(shp.JSON)["rings"]:
                xs = np.array([pt[0] for pt in part]); ys = np.array([pt[1] for pt in part])
                if np.sum(xs[:-1] * ys[1:] - xs[1:] * ys[:-1]) > 0:      # counter-clockwise = hole (Esri)
                    continue
                polys.append([[[round(pt[0], 4), round(pt[1], 4)] for pt in part]])
            g.append({"type": "Feature", "properties": {"pcode": int(code), "pname": names.get(int(code), ""),
                      "contrib_km2": round(float((pc == code).sum()) * cell_km2)},
                      "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    json.dump({"type": "FeatureCollection", "features": g}, open(os.path.join(out_static, "upstream_basins.geojson"), "w",
              encoding="utf8"), ensure_ascii=False, separators=(",", ":"))
    # exit points (first NS cell) with contributing area, for weighted accumulation at 30 m
    eu, ecnt = np.unique(exit_cell[cells], return_counts=True)
    er, ec = np.divmod(eu, nc)
    exits = [(xmin + (cc + 0.5) * cell, ymax - (rr + 0.5) * cell, int(round(n * cell_km2 / 0.0009)))
             for rr, cc, n in zip(er.tolist(), ec.tolist(), ecnt.tolist())]
    del fdir, prov, hexr, gidr, cnr, dn, step, nxt, dist, term, exit_cell, up

    # ---------------- 5. NS major-river HAND incl. upstream area ----------------
    _log("U5 NS weighted accumulation + HAND (major rivers incl. upstream)", log)
    for e in ("extent", "snapRaster", "cellSize"):
        arcpy.ClearEnvironment(e)
    q = lambda n: os.path.join(ws30, n + ".tif")
    arcpy.env.snapRaster = q("dem"); arcpy.env.extent = q("dem"); arcpy.env.cellSize = q("dem")
    pts_name = "entry_pts"; pts = os.path.join(gdb, pts_name)
    if arcpy.Exists(pts):
        arcpy.management.Delete(pts)
    arcpy.management.CreateFeatureclass(gdb, pts_name, "POINT", spatial_reference=UTM)
    arcpy.management.AddField(pts, "ext_cells", "LONG")
    with arcpy.da.InsertCursor(pts, ["SHAPE@XY", "ext_cells"]) as ic:
        for x, y, n in exits:
            ic.insertRow([(x, y), n])
    snap = SnapPourPoint(pts, q("facc"), 300, "ext_cells")
    w = Con(IsNull(snap), 1, snap + 1)
    faccw = FlowAccumulation(q("fdir"), w, "FLOAT", "D8"); faccw.save(q("facc_w"))
    Con(faccw > major_km2 / 0.0009, 1).save(q("major_w"))
    FlowDistance(q("major_w"), q("fill"), q("fdir"), "VERTICAL", "D8").save(q("hand_major_w"))

    ref = arcpy.Raster(q("dem")); ll = arcpy.Point(ref.extent.XMin, ref.extent.YMin)
    B = lambda n, nod: arcpy.RasterToNumPyArray(q(n), ll, ref.width, ref.height, nod)
    hid = B("hexid", -1).astype(np.int32); m = hid >= 0; z = hid[m]
    NH = int(z.max()) + 1
    fw = np.nan_to_num(B("facc_w", np.nan)[m], nan=0)
    hm = B("hand_major_w", np.nan)[m]
    facc_w = np.zeros(NH); np.maximum.at(facc_w, z, fw)
    order = np.lexsort((hm, z)); zs = z[order]; hs = hm[order]
    st = np.searchsorted(zs, np.arange(NH)); en = np.searchsorted(zs, np.arange(NH), "right")
    qs = (5, 10, 25, 50, 75); pct = np.full((len(qs), NH), -1.0)
    for i in range(NH):
        seg = hs[st[i]:en[i]]; seg = seg[~np.isnan(seg)]
        if seg.size:
            pct[:, i] = np.percentile(seg, qs)
    prm = json.load(open(os.path.join(out_static, "params.json")))
    prm.setdefault("facc_local_km2", prm["facc_km2"])
    prm["facc_km2"] = np.round(facc_w * 0.0009, 2).tolist()
    for k, qv in enumerate(qs):
        prm[f"handM_p{qv}"] = np.round(pct[k], 2).tolist()
    json.dump(prm, open(os.path.join(out_static, "params.json"), "w"), separators=(",", ":"))
    _log(f"done: zones {zones['n']}, entries {len(elist)}, major-river hexes with HAND "
         f"{int((pct[1] >= 0).sum())}/{NH}", log)
    return out_static
