# -*- coding: utf-8 -*-
"""
Nakhon Sawan Flood Watch — DEM conditioning + hex flow network + drainage assets (ArcGIS Pro 3.x)

1) condition_dem : FABDEM (Copernicus ที่ตัดอาคาร/ต้นไม้ออก) 30 ม. + burn ลำน้ำ/คลอง OSM + ยกคันกั้นน้ำ OSM
                   -> static_rasters/dem.tif (ใช้แทน DSM เดิม แล้ว build_static คำนวณ Fill/FlowDir/HAND ใหม่)
2) build_network : จาก raster 30 ม.
     - ขอบเชื่อม (edge) ระหว่าง hex ที่ติดกัน + ระดับจุดล้น (sill = P5 ของความสูงคู่ cell บนแนวขอบ)
     - สัดส่วนการไหลหลายทิศ: น้ำที่ไหลตาม D8 ออกจาก hex แยกตามปลายทาง (ถ่วงด้วย flow accumulation)
     - hypsometry: ความสูง P0..P100 ของ DEM ใน hex (stage-storage สำหรับระดับผิวน้ำ)
     - โครงสร้างระบายน้ำ: คลอง/คูระบาย (กม./hex), สถานีสูบ, ประตูระบายน้ำ, คันกั้นน้ำ (OSM + ไฟล์ผู้ใช้)
   ผลลัพธ์: data/static/network.json, drainage_assets.json, drainage.geojson, params.json (เพิ่ม z_p*)
"""
import os, json, time, io, zipfile, urllib.request, urllib.parse, csv
import numpy as np
import arcpy
from arcpy.sa import Con, IsNull, Raster, ExtractByMask

UTM = arcpy.SpatialReference(32647)
WGS = arcpy.SpatialReference(4326)
FAB = "https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn/"
FAB_ZIP = {"E099": "N10E090-N20E100_FABDEM_V1-2.zip", "E100": "N10E100-N20E110_FABDEM_V1-2.zip",
           "E101": "N10E100-N20E110_FABDEM_V1-2.zip"}
BURN = {"river": 3.0, "canal": 1.5, "stream": 1.5, "drain": 1.0, "ditch": 0.5}
DYKE_RAISE = 1.0
PUMP_DEFAULT_M3S = 3.0
ZQ = [0, 2, 5, 10, 25, 50, 75, 90, 100]


def _log(m, log=None):
    (log or print)(time.strftime("%H:%M:%S ") + str(m))


class _HttpFile(io.RawIOBase):
    """อ่าน zip บนเว็บแบบ HTTP Range (ดึงเฉพาะ tile ที่ต้องใช้จาก zip ขนาด GB)"""
    def __init__(self, url):
        self.url, self.pos = url, 0
        self.size = int(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30).headers["Content-Length"])
    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos
    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off); return self.pos
    def readinto(self, b):
        if not len(b) or self.pos >= self.size:
            return 0
        end = min(self.pos + len(b), self.size) - 1
        d = urllib.request.urlopen(urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{end}"}), timeout=180).read()
        b[:len(d)] = d; self.pos += len(d); return len(d)


def download_fabdem(src, tiles=("N15E099", "N16E099", "N15E100", "N16E100"), log=None):
    for t in tiles:
        out = os.path.join(src, f"fab_{t}.tif")
        if os.path.exists(out):
            continue
        _log(f"FABDEM {t}", log)
        z = zipfile.ZipFile(io.BufferedReader(_HttpFile(FAB + FAB_ZIP[t[-4:]]), buffer_size=8 << 20))
        with z.open(f"{t}_FABDEM_V1-2.tif") as fi, open(out + ".part", "wb") as fo:
            while True:
                b = fi.read(8 << 20)
                if not b:
                    break
                fo.write(b)
        os.replace(out + ".part", out)


OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]


def _overpass(q, log=None):
    """เรียก Overpass (ลองหลาย endpoint); ถ้าล้มเหลวทั้งหมดคืนค่าว่างพร้อมเตือน เพื่อไม่ให้ทั้ง build ค้าง"""
    err = None
    for url in OVERPASS:
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, data=("data=" + urllib.parse.quote(q)).encode(),
                                                              headers={"User-Agent": "NSFloodWatch/1.0"}), timeout=150)
            return json.loads(r.read().decode())
        except Exception as e:  # noqa
            err = e; time.sleep(3)
    _log(f"   ! Overpass ล้มเหลว (ข้าม tile นี้): {repr(err)[:100]}", log)
    return {"elements": []}


def download_osm(src, bbox=(15.0, 99.0, 16.45, 100.95), log=None, force=False, split=3):
    """ดึง OSM ผ่าน Overpass แบบแบ่ง tile (กัน timeout) แล้วรวม/ตัดซ้ำตาม id"""
    Q = {"osm_waterways": 'way["waterway"~"^(river|stream|canal|drain|ditch)$"]({b});out geom;',
         "osm_dykes": '(way["man_made"~"^(dyke|embankment)$"]({b});way["embankment"="yes"]({b});way["dike"]({b}););out geom;',
         "osm_pumps": 'nwr["man_made"="pumping_station"]({b});out center tags;',
         "osm_gates": 'nwr["waterway"~"^(sluice_gate|lock_gate|weir|dam)$"]({b});out center tags;'}
    s0, w0, n0, e0 = bbox
    for k, body in Q.items():
        p = os.path.join(src, k + ".json")
        if os.path.exists(p) and not force:
            continue
        _log(f"OSM {k}", log)
        els = {}
        sp = split if k == "osm_waterways" else 1                  # ชั้นเบาดึงทีเดียวทั้งพื้นที่
        ys = np.linspace(s0, n0, sp + 1); xs = np.linspace(w0, e0, sp + 1)
        for i in range(sp):
            for j in range(sp):
                b = f"{ys[i]:.3f},{xs[j]:.3f},{ys[i + 1]:.3f},{xs[j + 1]:.3f}"
                js = _overpass("[out:json][timeout:120];" + body.format(b=b), log)
                for el in js["elements"]:
                    els[(el["type"], el["id"])] = el
                time.sleep(1)
        json.dump({"elements": list(els.values())}, open(p, "w", encoding="utf8"), ensure_ascii=False)


def _osm_lines(js_path, fc, value_fn):
    """Overpass JSON (out geom) -> polyline FC (UTM) พร้อม field kind, val"""
    js = json.load(open(js_path, encoding="utf8"))
    gdb, name = os.path.split(fc)
    if arcpy.Exists(fc):
        arcpy.management.Delete(fc)
    arcpy.management.CreateFeatureclass(gdb, name, "POLYLINE", spatial_reference=UTM)
    arcpy.management.AddField(fc, "kind", "TEXT", field_length=32)
    arcpy.management.AddField(fc, "val", "DOUBLE")
    arcpy.management.AddField(fc, "name", "TEXT", field_length=128)
    n = 0
    with arcpy.da.InsertCursor(fc, ["SHAPE@", "kind", "val", "name"]) as ic:
        for el in js["elements"]:
            g = el.get("geometry")
            if not g or len(g) < 2:
                continue
            kind, val = value_fn(el.get("tags", {}))
            if val is None:
                continue
            line = arcpy.Polyline(arcpy.Array([arcpy.Point(p["lon"], p["lat"]) for p in g]), WGS).projectAs(UTM)
            ic.insertRow([line, kind, val, (el.get("tags", {}).get("name") or "")[:120]]); n += 1
    return n


def _osm_points(js_path):
    js = json.load(open(js_path, encoding="utf8"))
    out = []
    for el in js["elements"]:
        c = el.get("center") or ({"lat": el["lat"], "lon": el["lon"]} if "lat" in el else None)
        if c:
            t = el.get("tags", {})
            out.append({"lat": c["lat"], "lon": c["lon"], "name": t.get("name", ""), "tags": t, "osm": f'{el["type"]}/{el["id"]}'})
    return out


def condition_dem(project_dir, log=None):
    """FABDEM + burn waterways + raise dykes -> static_rasters/dem.tif ; ลบ raster อนุพันธ์เพื่อให้ build_static คำนวณใหม่"""
    arcpy.CheckOutExtension("Spatial"); arcpy.env.overwriteOutput = True; arcpy.env.pyramid = "NONE"
    src = os.path.join(project_dir, "source"); gdb = os.path.join(project_dir, "flood_static.gdb")
    ws = os.path.join(project_dir, "static_rasters"); p = lambda n: os.path.join(ws, n + ".tif")
    download_fabdem(src, log=log); download_osm(src, log=log)
    for e in ("extent", "snapRaster", "cellSize", "mask"):
        arcpy.ClearEnvironment(e)
    _log("N1 FABDEM mosaic/project 30 m", log)
    tiles = [os.path.join(src, f"fab_{t}.tif") for t in ("N15E099", "N16E099", "N15E100", "N16E100")]
    arcpy.management.MosaicToNewRaster(tiles, ws, "fab_wgs.tif", WGS, "32_BIT_FLOAT", None, 1, "MEAN")
    old = p("dem")
    if arcpy.Exists(old) and not arcpy.Exists(p("dem_copernicus")):
        arcpy.management.CopyRaster(old, p("dem_copernicus"))
    ref = p("dem_copernicus") if arcpy.Exists(p("dem_copernicus")) else None
    if ref:
        arcpy.env.snapRaster = ref; arcpy.env.extent = ref; arcpy.env.cellSize = ref
    arcpy.management.ProjectRaster(p("fab_wgs"), p("fab_utm"), UTM, "BILINEAR", "30 30")
    fab = ExtractByMask(p("fab_utm"), os.path.join(gdb, "prov_buf"))

    _log("N2 burn OSM waterways / raise dykes", log)
    ww = os.path.join(gdb, "osm_waterways"); dk = os.path.join(gdb, "osm_dykes")
    nw = _osm_lines(os.path.join(src, "osm_waterways.json"), ww,
                    lambda t: (t.get("waterway"), BURN.get(t.get("waterway"))))
    nd = _osm_lines(os.path.join(src, "osm_dykes.json"), dk, lambda t: ("dyke", DYKE_RAISE))
    arcpy.conversion.PolylineToRaster(ww, "val", p("burn"), "MAXIMUM_LENGTH", "", 30)
    dem = Con(IsNull(Raster(p("burn"))), fab, fab - Raster(p("burn")))
    if nd:
        arcpy.conversion.PolylineToRaster(dk, "val", p("dyke"), "MAXIMUM_LENGTH", "", 30)
        dem = Con(IsNull(Raster(p("dyke"))) | ~IsNull(Raster(p("burn"))), dem, dem + Raster(p("dyke")))
    tmp = p("dem_cond"); dem.save(tmp)
    for n in ("fill", "fdir", "facc", "stream", "major", "hand", "hand_major", "sink", "slope", "cn", "hexid",
              "hand_prov", "hand_wm", "facc_w", "major_w", "hand_major_w", "dem"):
        if arcpy.Exists(p(n)):
            arcpy.management.Delete(p(n))
    arcpy.management.CopyRaster(tmp, p("dem"))
    _log(f"   waterways {nw}, dykes {nd} -> static_rasters/dem.tif (FABDEM conditioned)", log)
    return p("dem")


def build_network(project_dir, repo_dir, log=None):
    arcpy.CheckOutExtension("Spatial"); arcpy.env.overwriteOutput = True
    src = os.path.join(project_dir, "source"); gdb = os.path.join(project_dir, "flood_static.gdb")
    ws = os.path.join(project_dir, "static_rasters"); q = lambda n: os.path.join(ws, n + ".tif")
    out_static = os.path.join(repo_dir, "data", "static")
    for e in ("extent", "snapRaster", "cellSize", "mask"):
        arcpy.ClearEnvironment(e)
    ref = arcpy.Raster(q("dem")); ll = arcpy.Point(ref.extent.XMin, ref.extent.YMin)
    nr, nc = ref.height, ref.width; xmin, ymax = ref.extent.XMin, ref.extent.YMax
    B = lambda n, nod: arcpy.RasterToNumPyArray(q(n), ll, nc, nr, nod)
    _log("N3 edges / sills / multi-direction partition", log)
    hid = B("hexid", -1).astype(np.int32)
    NH = int(hid.max()) + 1
    z = B("dem", np.nan).astype(np.float32)
    keys, zz = [], []
    for a, b, za, zb in ((hid[:, :-1], hid[:, 1:], z[:, :-1], z[:, 1:]), (hid[:-1, :], hid[1:, :], z[:-1, :], z[1:, :])):
        m = (a != b) & (a >= 0) & (b >= 0) & ~np.isnan(za) & ~np.isnan(zb)
        lo = np.minimum(a[m], b[m]).astype(np.int64); hi = np.maximum(a[m], b[m]).astype(np.int64)
        keys.append(lo * NH + hi); zz.append(np.maximum(za[m], zb[m]))
    keys = np.concatenate(keys); zz = np.concatenate(zz)
    order = np.lexsort((zz, keys)); keys = keys[order]; zz = zz[order]
    uk, st, cnt = np.unique(keys, return_index=True, return_counts=True)
    sill = zz[st + (cnt * 0.05).astype(int)]                      # P5 ของความสูงคู่ cell บนแนวขอบ (จุดล้น)
    ea, eb = (uk // NH).astype(int), (uk % NH).astype(int)
    width = cnt * 30.0
    # D8 crossings weighted by flow accumulation
    fdir = B("fdir", 0).astype(np.int32); facc = np.nan_to_num(B("facc", np.nan), nan=0).astype(np.float64)
    D8 = {1: (0, 1), 2: (1, 1), 4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1), 64: (-1, 0), 128: (-1, 1)}
    flows = {}
    out_edge = np.zeros(NH)
    for code, (dr, dc) in D8.items():
        rr, cc = np.where((fdir == code) & (hid >= 0))
        r2, c2 = rr + dr, cc + dc
        ok = (r2 >= 0) & (r2 < nr) & (c2 >= 0) & (c2 < nc)
        h1 = hid[rr, cc]; w = facc[rr, cc] + 1
        h2 = np.full(rr.size, -1); h2[ok] = hid[r2[ok], c2[ok]]
        cross = h1 != h2
        leave = cross & (h2 < 0)
        np.add.at(out_edge, h1[leave], w[leave])
        m = cross & (h2 >= 0)
        k = h1[m].astype(np.int64) * NH + h2[m]
        uk2, inv = np.unique(k, return_inverse=True)
        s = np.bincount(inv, weights=w[m])
        for kk, vv in zip(uk2.tolist(), s.tolist()):
            flows[kk] = flows.get(kk, 0) + vv
    tot = out_edge.copy()
    for kk, vv in flows.items():
        tot[kk // NH] += vv
    fsrc, fdst, ffrac = [], [], []
    for kk, vv in flows.items():
        a, b = kk // NH, kk % NH
        f = vv / tot[a] if tot[a] > 0 else 0
        if f >= 0.02:
            fsrc.append(a); fdst.append(b); ffrac.append(f)
    for a in np.where(out_edge > 0)[0]:
        f = out_edge[a] / tot[a]
        if f >= 0.02:
            fsrc.append(int(a)); fdst.append(-1); ffrac.append(float(f))
    fsrc = np.array(fsrc); fdst = np.array(fdst); ffrac = np.array(ffrac)
    s = np.bincount(fsrc, weights=ffrac, minlength=NH)
    ffrac = ffrac / np.maximum(s[fsrc], 1e-9)                       # normalise per source hex

    _log("N4 hex hypsometry", log)
    m = hid >= 0; zh = z[m]; hz = hid[m]
    ok = ~np.isnan(zh); zh = zh[ok]; hz = hz[ok]
    order = np.lexsort((zh, hz)); zh = zh[order]; hz = hz[order]
    st2 = np.searchsorted(hz, np.arange(NH)); en2 = np.searchsorted(hz, np.arange(NH), "right")
    zq = np.full((len(ZQ), NH), np.nan)
    for i in range(NH):
        seg = zh[st2[i]:en2[i]]
        if seg.size:
            zq[:, i] = np.percentile(seg, ZQ)
    del z, fdir, facc

    _log("N5 drainage assets (OSM + user CSV)", log)
    prm = json.load(open(os.path.join(out_static, "params.json")))
    for k, qv in enumerate(ZQ):
        prm[f"z_p{qv}"] = np.round(zq[k], 2).tolist()
    # canal / drain length per hex
    canal_km = np.zeros(NH)
    ww = os.path.join(gdb, "osm_waterways")
    if arcpy.Exists(ww):
        lyr = arcpy.management.MakeFeatureLayer(ww, "ww_lyr", "kind IN ('canal','drain','ditch')")
        inter = os.path.join(gdb, "canal_hex")
        arcpy.analysis.PairwiseIntersect([lyr, os.path.join(gdb, "hex")], inter)
        with arcpy.da.SearchCursor(inter, ["hid", "SHAPE@LENGTH"]) as cur:
            for h, L in cur:
                canal_km[h] += L / 1000.0
        arcpy.management.Delete(lyr)
    prm["canal_km"] = np.round(canal_km, 2).tolist()
    json.dump(prm, open(os.path.join(out_static, "params.json"), "w"), separators=(",", ":"))

    def hex_at(lon, lat):
        pt = arcpy.PointGeometry(arcpy.Point(lon, lat), WGS).projectAs(UTM).firstPoint
        c = int((pt.X - xmin) / 30); r = int((ymax - pt.Y) / 30)
        if 0 <= r < nr and 0 <= c < nc:
            return int(hid[r, c])
        return -1

    lon = np.asarray(prm["lon"]); lat = np.asarray(prm["lat"]); facc_km2 = np.asarray(prm["facc_km2"])
    major = np.where(facc_km2 >= 1000)[0]
    down = np.asarray(prm["down"])

    def outlet_of(h):
        if major.size:
            d = np.hypot((lon[major] - lon[h]) * 107.1, (lat[major] - lat[h]) * 110.6)
            j = int(d.argmin())
            if d[j] <= 5:
                return int(major[j])
        return int(down[h])

    assets = {"pumps": [], "gates": [], "note": "แก้ไข/เพิ่มได้ใน data/static/drainage_assets_user.csv (type,name,lon,lat,capacity_m3s)"}
    seen = set()
    for pnt in _osm_points(os.path.join(src, "osm_pumps.json")):
        h = hex_at(pnt["lon"], pnt["lat"])
        if h < 0 or (round(pnt["lat"], 3), round(pnt["lon"], 3)) in seen:
            continue
        seen.add((round(pnt["lat"], 3), round(pnt["lon"], 3)))
        cap = pnt["tags"].get("capacity") or pnt["tags"].get("pump:capacity")
        try:
            cap = float(str(cap).split()[0])
        except Exception:  # noqa
            cap = PUMP_DEFAULT_M3S
        assets["pumps"].append({"name": pnt["name"] or "สถานีสูบน้ำ", "lon": pnt["lon"], "lat": pnt["lat"], "hex": h,
                                "outlet": outlet_of(h), "cap_m3s": cap, "src": "OSM " + pnt["osm"], "cap_default": cap == PUMP_DEFAULT_M3S})
    for pnt in _osm_points(os.path.join(src, "osm_gates.json")):
        h = hex_at(pnt["lon"], pnt["lat"])
        if h < 0:
            continue
        kind = pnt["tags"].get("waterway", "sluice_gate")
        assets["gates"].append({"name": pnt["name"] or {"weir": "ฝาย", "dam": "เขื่อน/ฝาย", "lock_gate": "ประตูเรือสัญจร"}.get(kind, "ประตูระบายน้ำ"),
                                "kind": kind, "lon": pnt["lon"], "lat": pnt["lat"], "hex": h, "src": "OSM " + pnt["osm"]})
    ucsv = os.path.join(out_static, "drainage_assets_user.csv")
    if not os.path.exists(ucsv):
        with open(ucsv, "w", encoding="utf-8-sig", newline="") as f:
            f.write("type,name,lon,lat,capacity_m3s,note\n"
                    "# ตัวอย่าง: pump,สถานีสูบน้ำ...,100.1234,15.7012,6.0,ข้อมูลจากเทศบาล\n")
    else:
        for row in csv.DictReader(line for line in open(ucsv, encoding="utf-8-sig") if not line.startswith("#")):
            try:
                lo_, la_ = float(row["lon"]), float(row["lat"]); h = hex_at(lo_, la_)
            except Exception:  # noqa
                continue
            if h < 0:
                continue
            if row["type"].strip() == "pump":
                assets["pumps"].append({"name": row["name"], "lon": lo_, "lat": la_, "hex": h, "outlet": outlet_of(h),
                                        "cap_m3s": float(row.get("capacity_m3s") or PUMP_DEFAULT_M3S), "src": "user", "cap_default": False})
            elif row["type"].strip() == "gate":
                assets["gates"].append({"name": row["name"], "kind": "sluice_gate", "lon": lo_, "lat": la_, "hex": h, "src": "user"})
    json.dump(assets, open(os.path.join(out_static, "drainage_assets.json"), "w", encoding="utf8"), ensure_ascii=False, indent=0)

    # lines for the web map (canals/drains + dykes), simplified
    feats = []
    for fc, kinds in ((ww, ("canal", "drain", "ditch")), (os.path.join(gdb, "osm_dykes"), ("dyke",))):
        if not arcpy.Exists(fc):
            continue
        with arcpy.da.SearchCursor(fc, ["kind", "name", "SHAPE@"], spatial_reference=WGS) as cur:
            for kind, name, shp in cur:
                if kind not in kinds or shp is None:
                    continue
                shp = shp.generalize(0.0003)
                for part in json.loads(shp.JSON)["paths"]:
                    feats.append({"type": "Feature", "properties": {"k": kind, "n": name},
                                  "geometry": {"type": "LineString", "coordinates": [[round(p[0], 5), round(p[1], 5)] for p in part]}})
    json.dump({"type": "FeatureCollection", "features": feats}, open(os.path.join(out_static, "drainage.geojson"), "w", encoding="utf8"),
              ensure_ascii=False, separators=(",", ":"))

    net = {"n": NH, "edge_a": ea.tolist(), "edge_b": eb.tolist(), "sill": np.round(sill, 2).tolist(),
           "width_m": width.tolist(), "flow_src": fsrc.tolist(), "flow_dst": fdst.tolist(),
           "flow_frac": np.round(ffrac, 4).tolist()}
    json.dump(net, open(os.path.join(out_static, "network.json"), "w"), separators=(",", ":"))
    _log(f"done: edges {len(ea)}, flow links {len(fsrc)}, pumps {len(assets['pumps'])}, gates {len(assets['gates'])}, "
         f"canal {canal_km.sum():.0f} km", log)
    return net


def build_all(project_dir, repo_dir, log=None):
    """ลำดับเต็ม: DEM conditioning -> static -> upstream -> network"""
    import build_static, build_upstream
    condition_dem(project_dir, log)
    build_static.build(project_dir, repo_dir, log=log)
    build_upstream.build_upstream(project_dir, repo_dir, log=log)
    return build_network(project_dir, repo_dir, log)
