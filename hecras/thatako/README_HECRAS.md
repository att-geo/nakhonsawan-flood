# HEC-RAS 2D — ท่าตะโก + พระนอน/ทับกฤช (แอ่งรับน้ำ)

ไฟล์ในโฟลเดอร์นี้สร้างโดย ArcGIS Pro (arcgis/build_hotspots.py) สำหรับทำแบบจำลอง 2D rain-on-grid เทียบกับโมเดลในเว็บ

1. HEC-RAS 6.x → New Project → RAS Mapper → Projection: `WGS 84 / UTM zone 47N (EPSG:32647)`
2. Terrain → Create New Terrain → `terrain.tif` (FABDEM 30 ม. + burn ลำน้ำ/ยกคันกั้นน้ำจาก OSM)
3. Map Layers → Land Cover → `landcover.tif` แล้วกรอกค่า Manning's n จาก `manning.csv`
4. Geometry → 2D Flow Areas → Import `perimeter.shp` → cell 60–120 ม. + Refinement ตามแนวแม่น้ำ/ถนน
5. Boundary Condition Lines → import `bc_lines.shp` → Normal Depth (slope 0.001)
   และเพิ่ม Stage Hydrograph ที่ลำน้ำหลักจาก `stage_<สถานี>.csv`
6. Unsteady Flow → Meteorological Data → Precipitation = Point/Constant → `rain.csv` (มม./ชม. เฉลี่ยทั้งโดเมน)
   หรือ Gridded จาก Open-Meteo, Infiltration = SCS Curve Number (ใช้ raster cn จาก ArcGIS Pro)
7. Compute (Full Momentum / Diffusion Wave) แล้วเทียบ max depth กับ `data/live/hotspots/thatako_max72.png`

`rain.csv`, `stage_*.csv` ถูกอัปเดตทุกครั้งที่รัน `python pipeline/run_hotspots.py --site . --hecras`

## โดเมนแอ่งรับน้ำท่าตะโก

- `perimeter.shp` = กรอบทั้งพื้นที่รับน้ำ (~81 × 73 กม.) — ท่าตะโกรับน้ำจากไพศาลี หนองบัว ชุมแสง พยุหะคีรี ตากฟ้า
  ถ้าย่อโดเมน ให้ใช้ `contributing_area.shp` + buffer ≥ 3 กม. และ**ต้องครอบแม่น้ำน่านช่วงชุมแสง–ปากน้ำโพ** (ทางระบายหลักของบึงบอระเพ็ด/ท่าตะโก)
- `aoi_tambon.shp` = ตำบลที่ศึกษา (ท่าตะโก 10 ตำบล + พระนอน + ทับกฤช + ไผ่สิงห์) → RAS Mapper: Results → Calculate Profile/Zonal stats
- ใส่ Stage Hydrograph ที่แม่น้ำน่าน `stage_N67.csv` (ต้นน้ำ, ชุมแสง) และ `stage_C2.csv` (ท้ายน้ำ, ปากน้ำโพ)
- ระยะเวลาท่วมขัง: Unsteady → Simulation Time ≥ 14 วัน, RAS Mapper → Duration (depth ≥ 0.1 ม.) และ Arrival/Recession Time
  เทียบกับ `data/live/hotspots/thatako_dur.png` และตาราง `ponding` ใน `hotspots_live.json`
