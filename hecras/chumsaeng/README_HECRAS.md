# HEC-RAS 2D — ชุมแสง (น่าน–ยม)

ไฟล์ในโฟลเดอร์นี้สร้างโดย ArcGIS Pro (arcgis/build_hotspots.py) สำหรับทำแบบจำลอง 2D rain-on-grid เทียบกับโมเดลในเว็บ

1. HEC-RAS 6.x → New Project → RAS Mapper → Projection: `WGS 84 / UTM zone 47N (EPSG:32647)`
2. Terrain → Create New Terrain → `terrain.tif` (FABDEM 30 ม. + burn ลำน้ำ/ยกคันกั้นน้ำจาก OSM)
3. Map Layers → Land Cover → `landcover.tif` แล้วกรอกค่า Manning's n จาก `manning.csv`
4. Geometry → 2D Flow Areas → Import `perimeter.shp` → cell 60–120 ม. + Refinement ตามแนวแม่น้ำ/ถนน
5. Boundary Condition Lines → import `bc_lines.shp` → Normal Depth (slope 0.001)
   และเพิ่ม Stage Hydrograph ที่ลำน้ำหลักจาก `stage_<สถานี>.csv`
6. Unsteady Flow → Meteorological Data → Precipitation = Point/Constant → `rain.csv` (มม./ชม. เฉลี่ยทั้งโดเมน)
   หรือ Gridded จาก Open-Meteo, Infiltration = SCS Curve Number (ใช้ raster cn จาก ArcGIS Pro)
7. Compute (Full Momentum / Diffusion Wave) แล้วเทียบ max depth กับ `data/live/hotspots/chumsaeng_max72.png`

`rain.csv`, `stage_*.csv` ถูกอัปเดตทุกครั้งที่รัน `python pipeline/run_hotspots.py --site . --hecras`
