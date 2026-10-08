# -*- coding: utf-8 -*-
"""Publish ผลวิเคราะห์ NakhonSawan Flood ขึ้น ArcGIS Enterprise Portal (ใช้ active portal ของ ArcGIS Pro)

ขั้นตอน (รันใน Python ของ ArcGIS Pro):
  1) python pipeline/portal_layers.py --site . --out ../portal_out      (สร้าง Esri JSON)
  2) build_gdb(json_dir, work_dir)   -> NS_Flood_Analysis.gdb, NS_Flood_Live.gdb (alias ไทย)
  3) publish(work_dir, share="everyone") -> hosted feature layers 2 services, บันทึก item id ใน portal_items.json
"""
import json, os, shutil, zipfile
import arcpy

FT = {"esriFieldTypeString": "TEXT", "esriFieldTypeInteger": "LONG", "esriFieldTypeDouble": "DOUBLE",
      "esriFieldTypeDate": "DATE", "esriFieldTypeSmallInteger": "SHORT"}
GT = {"esriGeometryPolygon": "POLYGON", "esriGeometryPoint": "POINT", "esriGeometryPolyline": "POLYLINE"}
SR = arcpy.SpatialReference(4326)

LAYER_TITLE = {
    "hex_s1": "น้ำขังนานซ้ำซาก ราย hex (Sentinel-1 2560–2568)",
    "tambon_s1": "น้ำขังนานซ้ำซาก รายตำบล (Sentinel-1 2560–2568)",
    "district_s1": "น้ำขังนานซ้ำซาก รายอำเภอ (Sentinel-1 2560–2568)",
    "year_s1": "พื้นที่ท่วมนานรายปี ทั้งจังหวัด (Sentinel-1)",
    "tambon_events": "แบบจำลอง 2D เทียบน้ำท่วมจริง รายตำบล (2564/65/67/68)",
    "district_events": "แบบจำลอง 2D เทียบน้ำท่วมจริง รายอำเภอ (2564/65/67/68)",
    "year_events": "แบบจำลอง 2D เทียบน้ำท่วมจริง รายปี",
    "tambon_scn": "สถานการณ์สมมติ A/C/D/E รายตำบล",
    "district_scn": "สถานการณ์สมมติ A/C/D/E รายอำเภอ",
    "scn_total": "สถานการณ์สมมติ A/C/D/E ทั้งจังหวัด",
    "hex_status": "สถานการณ์น้ำท่วมขังล่าสุด ราย hex 1 กม² (อัปเดตทุกชั่วโมง)",
    "district_status": "สถานการณ์น้ำท่วมขังล่าสุด รายอำเภอ (อัปเดตทุกชั่วโมง)",
}
ORDER = {"analysis": ["hex_s1", "tambon_s1", "district_s1", "tambon_events", "district_events", "tambon_scn",
                      "district_scn", "year_s1", "year_events", "scn_total"],
         "live": ["hex_status", "district_status"]}
SERVICE = {"analysis": ("NakhonSawan_Flood_Analysis", "NS_Flood_Analysis.gdb",
                        "นครสวรรค์ น้ำท่วม — ผลวิเคราะห์ (Sentinel-1, แบบจำลอง 2D, สถานการณ์สมมติ)"),
           "live": ("NakhonSawan_Flood_Live", "NS_Flood_Live.gdb",
                    "นครสวรรค์ น้ำท่วม — สถานการณ์ล่าสุด (อัปเดตทุกชั่วโมง)")}


def load_json(p):
    return json.load(open(p, encoding="utf-8"))


def build_gdb(json_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True); out = {}
    for grp, names in ORDER.items():
        gdb = os.path.join(work_dir, SERVICE[grp][1])
        if arcpy.Exists(gdb): arcpy.management.Delete(gdb)
        arcpy.management.CreateFileGDB(work_dir, SERVICE[grp][1])
        for name in names:
            d = load_json(os.path.join(json_dir, grp, name + ".json"))
            gt = d["geometryType"]
            if gt in GT:
                arcpy.management.CreateFeatureclass(gdb, name, GT[gt], spatial_reference=SR)
            else:
                arcpy.management.CreateTable(gdb, name)
            tgt = os.path.join(gdb, name)
            arcpy.management.AddFields(tgt, [[f["name"], FT[f["type"]], f["alias"], f.get("length")] for f in d["fields"]])
            arcpy.AlterAliasName(tgt, LAYER_TITLE.get(name, name))
            names_f = [f["name"] for f in d["fields"]]; dates = {f["name"] for f in d["fields"] if f["type"] == "esriFieldTypeDate"}
            import datetime
            cols = names_f + (["SHAPE@"] if gt in GT else [])
            with arcpy.da.InsertCursor(tgt, cols) as cur:
                for ft in d["features"]:
                    a = ft["attributes"]
                    row = [(datetime.datetime.utcfromtimestamp(a[k] / 1000) if (k in dates and a.get(k)) else a.get(k)) for k in names_f]
                    if gt in GT:
                        g = dict(ft["geometry"]); g["spatialReference"] = {"wkid": 4326}
                        row.append(arcpy.AsShape(g, True))
                    cur.insertRow(row)
            out[f"{grp}/{name}"] = int(arcpy.management.GetCount(tgt)[0])
    return out


def _zip_gdb(gdb):
    z = gdb[:-4] + ".zip"
    if os.path.exists(z): os.remove(z)
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(gdb):
            for f in files:
                if f.endswith(".lock"): continue
                full = os.path.join(root, f)
                zf.write(full, os.path.join(os.path.basename(gdb), os.path.relpath(full, gdb)))
    return z


def publish(work_dir, share="everyone", folder="NakhonSawan Flood 2026", tags=None, ids_file=None):
    from arcgis.gis import GIS
    gis = GIS("pro"); me = gis.users.me
    tags = tags or ["นครสวรรค์", "น้ำท่วม", "flood", "Nakhon Sawan", "Sentinel-1", "NakhonsawanFlood2026"]
    try: gis.content.folders.create(folder)
    except Exception: pass
    ids_file = ids_file or os.path.join(work_dir, "portal_items.json")
    ids = load_json(ids_file) if os.path.exists(ids_file) else {}
    for grp in ("analysis", "live"):
        svc, gdbname, title = SERVICE[grp]
        z = _zip_gdb(os.path.join(work_dir, gdbname))
        fld = gis.content.folders.get(folder)
        job = fld.add({"title": svc + "_gdb", "type": "File Geodatabase", "tags": tags,
                       "snippet": title}, file=z)
        src = job.result() if hasattr(job, "result") else job
        fl = src.publish(publish_parameters={"name": svc, "maxRecordCount": 10000, "hasStaticData": grp == "analysis"},
                         file_type="fileGeodatabase")
        fl.update(item_properties={"title": title, "snippet": title, "tags": ",".join(tags),
                  "description": "ผลวิเคราะห์โปรเจกต์ Nakhon Sawan Flood Watch (ArcGIS Pro + pipeline บน GitHub Actions) — "
                                 "https://att-geo.github.io/nakhonsawan-flood/",
                  "accessInformation": "Copernicus Sentinel-1 (Microsoft Planetary Computer), FABDEM, ESA WorldCover, ThaiWater (สสน.), Open-Meteo, OCHA COD-AB"})
        if share:
            fl.sharing.sharing_level = share; src.sharing.sharing_level = "private"
        ids[grp] = {"item": fl.id, "url": fl.url, "gdb_item": src.id,
                    "layers": {l.properties.name: i for i, l in enumerate(fl.layers)},
                    "tables": {t.properties.name: i for i, t in enumerate(fl.tables)}}
        json.dump(ids, open(ids_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return ids
