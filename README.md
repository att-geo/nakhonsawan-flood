# Nakhon Sawan Flood Watch 🌧️🗺️

Web app ติดตาม **พื้นที่น้ำท่วมขัง ความลึก และระยะเวลาท่วม** ของจังหวัดนครสวรรค์ แบบ near-realtime (อัปเดตทุก 1 ชั่วโมง)
ชั้นข้อมูลภูมิประเทศวิเคราะห์ด้วย **ArcGIS Pro** ส่วนข้อมูลฝน/ระดับน้ำดึงอัตโนมัติจาก **Open-Meteo, ThaiWater (สสน.), GISTDA** และแสดง **Windy** ประกอบ
โฮสต์ฟรีบน **GitHub Pages** + **GitHub Actions**

## สถาปัตยกรรม

```mermaid
flowchart LR
  subgraph PRO["ArcGIS Pro (ทำครั้งเดียว / เมื่อปรับข้อมูลพื้นฐาน)"]
    DEM[Copernicus DEM 30 ม.] --> HYD[Fill · Flow Dir · Flow Acc<br/>HAND · Sink · Slope]
    WC[ESA WorldCover] --> CN[Curve Number<br/>HSG C/D]
    ADM[ขอบเขตจังหวัด/อำเภอ] --> HEX[Hex grid 1 กม²<br/>+ hex ท้ายน้ำ]
    HYD --> HEX
    CN --> HEX
  end
  HEX -->|data/static/*.json, png| REPO[(GitHub repo)]
  subgraph PRO2["ArcGIS Pro — ลุ่มน้ำต้นน้ำ"]
    DEM2[DEM ภูมิภาค 90 ม.<br/>5 จังหวัด] --> UP[ทางไหลเข้า นว. · จุดน้ำเข้า<br/>เวลาเดินทาง · zones]
    UP --> HM[HAND แม่น้ำสายหลัก<br/>รวมพื้นที่ต้นน้ำ]
  end
  UP -->|upstream_*.json| REPO
  HM --> REPO
  subgraph GHA["GitHub Actions — ทุกชั่วโมง"]
    OM[Open-Meteo<br/>ฝนรายชม. −30 วัน…+72 ชม.] --> MOD
    TWR[ThaiWater ฝน 24 ชม.<br/>ปรับแก้ฝนแบบจำลอง] --> MOD
    TWL[ThaiWater ระดับน้ำ/ตลิ่ง<br/>ปริมาณน้ำ RID 5 จังหวัด] --> MOD
    MOD2[น้ำหลาก 4 จังหวัดต้นน้ำ<br/>SCS-CN + lag + level-pool] --> MOD
    GIS[GISTDA flood extent<br/>ถ้ามี API key] --> OUT
    S1[Sentinel-1 SAR วันละ 2 รอบ<br/>Planetary Computer ไม่ต้องใช้ key] --> OUT
    MOD[โมเดล SCS-CN +<br/>hex routing + HAND riverine] --> OUT[data/live/*.json]
  end
  REPO --> GHA
  OUT --> PAGES[GitHub Pages<br/>Leaflet web app]
  WINDY[Windy embed] --> PAGES
```

| ชั้น | เทคโนโลยี | ไฟล์ |
|---|---|---|
| วิเคราะห์ภูมิประเทศ | ArcGIS Pro 3.x + Spatial Analyst | `arcgis/build_static.py`, `arcgis/NakhonSawanFlood.pyt` |
| ดึงข้อมูล + โมเดล | Python 3 (stdlib + numpy) | `pipeline/run_update.py`, `pipeline/model.py` |
| ตั้งเวลา + deploy | GitHub Actions + Pages | `.github/workflows/update.yml` |
| หน้าเว็บ | Leaflet 1.9 (ไม่มี build step) | `index.html`, `assets/` |

รายละเอียดสมการและเหตุผลการออกแบบ: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## ฟีเจอร์บนเว็บ

**สองโหมด** (ปุ่มบนสุดของแผง — จำค่าที่เลือกไว้ในเครื่อง, ลิงก์ `?view=expert` เปิดโหมดละเอียดโดยตรง)

- **ดูแบบง่าย (ค่าเริ่มต้น สำหรับประชาชนทั่วไป)** — ภาษาง่าย ไม่มีศัพท์เทคนิค:
  สถานการณ์จังหวัดเป็นสีสัญญาณ (ปกติ / เฝ้าระวัง / เตือนภัย / อันตราย), พื้นที่เป็น **ไร่**, ความลึกเทียบกับร่างกาย (**ข้อเท้า / เข่า / เอว / สูงกว่าเอว**) พร้อมรูปคน, เวลาเป็น **วัน** ;
  **บ้านของฉัน** (ใช้ตำแหน่ง GPS หรือแตะแผนที่แล้วบันทึก) บอกลึกแค่ไหน ท่วมมากี่วัน อีกกี่วันลด สาเหตุ ; รายอำเภอ (ความลึกส่วนใหญ่ + บางจุด, แนวโน้ม 3 วัน) ;
  แม่น้ำสายหลัก (ยังรับน้ำได้ / ใกล้เต็มตลิ่ง / ล้นตลิ่ง) ; ฝน ; สิ่งที่ควรทำ + เบอร์ฉุกเฉิน (1784, 1669, 191, 1460) ;
  ปุ่มใต้แผนที่ **ตอนนี้ / พรุ่งนี้ / มะรืนนี้ / อีก 3 วัน / ขังนานแค่ไหน / เคยท่วมนาน** ;
  **ในอดีต ที่ไหนน้ำขังนาน?** (ภาพดาวเทียม 9 ปี): ปีน้ำมาก, ที่ดินที่น้ำขังนานเกิน 1 เดือนรายปีและรายอำเภอเป็น **ไร่**, ตำบลที่ขังนานบ่อยที่สุด (แตะแล้วซูม), และบอกใน “บ้านของฉัน”/จุดที่แตะว่า **เคยน้ำขังเกิน 1 เดือนกี่ปีใน 9 ปี (บางปี / บ่อย / เกือบทุกปี)**
- **ดูแบบละเอียด (ผู้เชี่ยวชาญ)** — แท็บและตัวเลขเดิมทั้งหมดด้านล่าง

- แผนที่ hex 1 กม² แสดงระดับน้ำท่วมขัง 5 ชั้น พร้อม **แถบเวลา −7 วัน ถึง +72 ชม.** (กดเล่นเป็นแอนิเมชันได้)
- โหมดแผนที่: ท่วมขังตอนนี้ · สูงสุดใน 72 ชม. · ระยะเวลาท่วม · ฝน 24 ชม./7 วัน · ฝนพยากรณ์ 72 ชม.
- แท็บอำเภอ: พื้นที่ท่วม + **ระยะเวลาท่วม** (ท่วมมาแล้ว → คาดลดใน, แถบสีแยก < 1 วัน / 1–3 / 3–7 / 7–14 / > 14 วัน) และสรุปทั้งจังหวัด
- แท็บต้นน้ำ: hydrograph ค่าตรวจวัด 7 วัน + พยากรณ์ที่ลดตามอัตราน้ำลดจริงของสถานี, ล้นตลิ่งมาแล้วกี่ชั่วโมง, คาดต่ำกว่าความจุเมื่อไร
- คลิก hex: ความลึก, สาเหตุ (ฝน/ล้นตลิ่ง), **ท่วมมาแล้วกี่ชั่วโมง, คาดว่าจะลดลงเมื่อไร**, ฝนสะสม, ค่า HAND/CN จาก ArcGIS Pro
- **แท็บต้นน้ำ**: น้ำจากกำแพงเพชร พิจิตร พิษณุโลก เพชรบูรณ์ — ปริมาตรที่จะไหลเข้า นว. ใน 72 ชม. รายจังหวัด, hydrograph + พยากรณ์ของปิง/น่าน-ยม/แม่วงก์/เจ้าพระยา เทียบความจุลำน้ำ, พื้นที่ที่ราบลุ่มน้ำล้นตลิ่ง, แผนที่ลุ่มน้ำและจุดน้ำเข้า
- **แท็บ 2D จุดวิกฤต**: แผนที่ความลึกความละเอียด 120 ม. (ลาดยาว, เมืองนครสวรรค์, ชุมแสง) ตอนนี้/+24/+48/+72 ชม./สูงสุด, ชั้นคลอง คันกั้นน้ำ สถานีสูบ ประตูระบายน้ำ และผลการสอบเทียบ
- **แท็บท่วมซ้ำ (ทั้งจังหวัด)**: น้ำท่วมจริงจาก Sentinel-1 ปี 2560–2568 วงโคจร 62 + 172 — แผนที่จำนวนปีที่ท่วม ≥ 30 วัน, กราฟรายปี, ตารางรายอำเภอ/130 ตำบล (คลิกแล้วซูม), ข้อมูลใน popup ราย hex
- **แท็บท่าตะโก**: ชั้นแผนที่ขอบเขตตำบล + น้ำท่วมซ้ำ Sentinel-1 / เหตุการณ์ 64·65·68 / สถานการณ์ E (สลับได้), ระยะเวลาท่วมขังรายตำบล, น้ำท่วมจริงจาก Sentinel-1 2560–2568, **จำลองเหตุการณ์ปี 2564/2565/2568 เทียบดาวเทียม (แผนที่ตรง/ขาด/เกิน + กราฟรายวัน + ตารางรายตำบล)** และสถานการณ์สมมติ
- สรุปรายอำเภอ, สถานีระดับน้ำ (สถานการณ์ตามตลิ่ง), สถานีวัดฝน, กราฟฝนรายชั่วโมง, แท็บ Windy (ฝน/ฝนสะสม/เรดาร์/เมฆ)
- ชั้นพื้นที่ลุ่มต่ำ (HAND) จาก ArcGIS Pro, ชั้นน้ำท่วมจากดาวเทียม GISTDA (ถ้าเปิดใช้) และ **น้ำท่วมจาก Sentinel-1 ที่แปลเอง (ไม่ต้องใช้ key)**
- รีเฟรชข้อมูลเองทุก 5 นาที · ใช้บนมือถือได้

## วิธี deploy ขึ้น GitHub (ครั้งแรก ~10 นาที)

1. สร้าง repo ใหม่บน GitHub แบบ **Public** (เช่น `nakhonsawan-flood`) — ไม่ต้องติ๊ก add README
2. ในโฟลเดอร์นี้ (มี `git init` และ commit แรกไว้แล้ว):
   ```bash
   git remote add origin https://github.com/<user>/nakhonsawan-flood.git
   git push -u origin main
   ```
3. GitHub → **Settings → Pages → Build and deployment → Source: GitHub Actions**
4. **Actions** → เลือก `update-flood-data` → **Run workflow** (ครั้งแรก) — จากนั้นจะรันเองทุกชั่วโมง
5. เปิด `https://<user>.github.io/nakhonsawan-flood/`

**น้ำท่วมจาก Sentinel-1 (เปิดอยู่แล้ว ไม่ต้องใช้ key)** — workflow ดึงภาพ `sentinel-1-rtc` จาก Microsoft Planetary Computer วันละ 2 ครั้ง (03:00 และ 15:00 UTC) แปลน้ำท่วมด้วย change detection + Otsu แล้วสะสมเป็นสัดส่วนท่วมราย hex (`data/live/s1_obs.json`) ใช้สอบเทียบร่วมกับ GISTDA/2D (รายละเอียด: `docs/ARCHITECTURE.md` หัวข้อ 4f) ; รันเองได้:
```bash
pip install numpy scipy rasterio pyproj pystac-client planetary-computer
python pipeline/s1_obs.py --site . --days 45          # ครั้งแรกย้อนหลัง 45 วัน
# ภาพที่ประมวลผลเองใน SNAP / ArcGIS Pro (terrain corrected, linear หรือ dB):
python pipeline/s1_obs.py --site . --local-flood s1_flood_vv.tif --local-ref s1_ref_vv.tif --time 2026-09-25T06:10+07:00
```

(ตัวเลือก) เปิดชั้นน้ำท่วมจากดาวเทียม GISTDA: สมัคร API key ที่ [GISTDA Sphere / Disaster API](https://sphere.gistda.or.th/docs/web-service/disaster-information) แล้วใส่ใน **Settings → Secrets and variables → Actions → New secret** ชื่อ `GISTDA_API_KEY`

เมื่อเปิดแล้ว ระบบจะสะสมภาพน้ำท่วมรายวันเป็นสัดส่วนท่วมราย hex (`data/live/gistda_obs.json`) และใช้เป็นเป้าหมายสอบเทียบร่วมกับแบบจำลอง 2D ในรอบสอบเทียบรายสัปดาห์ (รายละเอียดและข้อจำกัดเรื่องเวลา/การครอบคลุมของภาพ: `docs/ARCHITECTURE.md` หัวข้อ 4e) ตรวจรูปแบบข้อมูลจริงของ API ได้ด้วย `python pipeline/gistda_obs.py --probe data/live/gistda_flood.geojson`

> ⚠️ GitHub ปิด scheduled workflow อัตโนมัติถ้า repo ไม่มี commit 60 วัน — workflow นี้มีขั้นตอน keepalive ให้แล้ว
> ⚠️ ถ้า runner ของ GitHub (อยู่ต่างประเทศ) ดึง ThaiWater ไม่ได้ ระบบจะยังทำงานด้วย Open-Meteo อย่างเดียว (ดูสถานะในแท็บ "วิธีการ") หรือใช้ self-hosted runner / รัน tool ที่ 2 ใน ArcGIS Pro แทน

## การใช้งานใน ArcGIS Pro

Catalog → Toolboxes → Add Toolbox → `arcgis/NakhonSawanFlood.pyt`

| Tool | ทำอะไร |
|---|---|
| **0) Build All** | FABDEM + burn ลำน้ำ/คันกั้นน้ำ OSM → 1 → 1b → 1c → 1d (ชั้นข้อมูลคงที่ใหม่ทั้งหมด, 30–60 นาที) |
| 1) Build Static Layers | Fill/FlowDir/FlowAcc/HAND/แอ่ง/CN → hex 1 กม² |
| 1b) Build Upstream Basins | ลุ่มน้ำกำแพงเพชร พิจิตร พิษณุโลก เพชรบูรณ์ ที่ไหลเข้า นว., จุดน้ำเข้า, เวลาเดินทาง, HAND แม่น้ำสายหลัก |
| 1c) Build Network & Drainage | การไหลหลายทิศ, จุดล้นระหว่าง hex, hypsometry, คลอง/สถานีสูบ/ประตูน้ำ (รันใหม่หลังแก้ `drainage_assets_user.csv`) |
| 1d) Build 2D Hotspots | โดเมนแบบจำลอง 2D (ลาดยาว เมือง ชุมแสง **แอ่งท่าตะโก**) + ชุดข้อมูล HEC-RAS (`hecras/`) |
| 2) Run Update (local) | รันโมเดลบนเครื่อง → feature class `hex_status` |
| 2b) Run 2D Hotspots | แบบจำลอง 2D + rain.csv / stage_*.csv สำหรับ HEC-RAS ; ท่าตะโกรันต่อ 14 วันหาระยะเวลาท่วมขังรายตำบล |
| 2c) Calibrate | สอบเทียบโมเดล hex กับผล 2D → `calibration.json` |
| 3) Publish to GitHub | commit + push |

Command line: `python pipeline/run_update.py --site .` → `python pipeline/run_hotspots.py --site . [--long]` → `python pipeline/calibrate.py --site .`
น้ำท่วมในอดีตจาก Sentinel-1 **ทั้งจังหวัด** (วงโคจร 62 + 172, ~20 นาทีโหลด + ~25 นาทีวิเคราะห์, ต้องมี rasterio/scipy/shapely): `python pipeline/s1_province.py --out ../s1_prov --site . --download` → `python pipeline/s1_province_web.py --out ../s1_prov --site .` (รายละเอียด `docs/ARCHITECTURE.md` 4h)
น้ำท่วมในอดีตจาก Sentinel-1 แอ่งท่าตะโก (2560–2568, รันใน Python ของ ArcGIS Pro): `python pipeline/s1_history.py --out ../s1_hist --site . --download`
จำลองเหตุการณ์จริงย้อนหลัง (ปี 2564/2565/2568, ~30 นาที/ปี) + เทียบดาวเทียม: `python pipeline/event_2d.py --site . --year 2025 --bank-off -1 --in-cap 400 --hold-level 24.5 --loss 0.08 --n-mult 2 --s1-dir ../s1_hist` → `python pipeline/event_compare.py --site . --s1 ../s1_hist --run hecras/thatako_ev/runs/2025.npz`
สรุปผลเหตุการณ์จริงขึ้นเว็บ (แท็บ 2D → แอ่งท่าตะโก): `python pipeline/events_web.py --site . --s1 ../s1_hist` → `data/static/hotspots/thatako_events.json`, `ev/thatako_ev_<ปี>.png`
สถานการณ์สมมติแอ่งท่าตะโก (ฝน × ระดับแม่น้ำ, ~25 นาที/สถานการณ์): `python pipeline/scenario_2d.py --site . --rain 250 --rain-days 5 --tag _r250` (รายละเอียด `docs/ARCHITECTURE.md` 4g)
ทดสอบเว็บ: `python -m http.server 8000`

**เพิ่มข้อมูลโครงสร้างระบายน้ำจริง** (ความจุสถานีสูบ, ประตูระบายน้ำที่ OSM ไม่มี): แก้ `data/static/drainage_assets_user.csv`
(`type,name,lon,lat,capacity_m3s,note` — type = pump หรือ gate) แล้วรัน tool 1c

## โครงสร้างโฟลเดอร์

```
index.html, assets/app.js, assets/app.css   หน้าเว็บ
data/static/   hex.geojson, params.json, districts.geojson, province.geojson,
               susceptibility.png/.json, stations_ref.json,
               province_tambon.geojson, tambon_web.geojson, s1_province.json/_hex.json/_freq.png  ← น้ำท่วมจริง S1 ทั้งจังหวัด,
               upstream_zones.json, upstream_entries.json, upstream_basins.geojson   ← จาก ArcGIS Pro
data/live/     meta, status, frames, stations, districts, series, upstream,
               gauges_hist, rain_cache (.json)                             ← จาก pipeline ทุกชั่วโมง
pipeline/      run_update.py (ดึงข้อมูล + เขียนผล), model.py (โมเดล hex v3), upstream.py (น้ำหลากจากต้นน้ำ), gauges.py (ประวัติสถานี/rating/recession),
               model2d.py + run_hotspots.py (แบบจำลอง 2D), scenario_2d.py (สถานการณ์สมมติ), event_2d.py + event_compare.py + events_web.py (เหตุการณ์จริง vs Sentinel-1), calibrate.py (สอบเทียบ)
arcgis/        build_static.py, build_upstream.py, build_network.py, build_hotspots.py, NakhonSawanFlood.pyt
hecras/        ชุดข้อมูลสำหรับ HEC-RAS 2D ของแต่ละจุดวิกฤต
docs/          ARCHITECTURE.md
```

## ข้อจำกัด

โมเดล hex เป็น **screening model** ความละเอียด 1 กม² (มีแบบจำลอง 2D 120 ม. เฉพาะจุดวิกฤต) สำหรับเตือนภัยล่วงหน้าและจัดลำดับพื้นที่เฝ้าระวัง ไม่ใช่แบบจำลองชลศาสตร์ 2 มิติ
ความลึกเป็นค่าประมาณ ควรสอบเทียบกับพื้นที่น้ำท่วมจริงจาก GISTDA/Sentinel-1/รายงานภาคสนามก่อนใช้ประกอบการตัดสินใจ

## แหล่งข้อมูลและสัญญาอนุญาต

Open-Meteo (CC BY 4.0, non-commercial ฟรี) · ThaiWater / สถาบันสารสนเทศทรัพยากรน้ำ (สสน.) · GISTDA · Copernicus Sentinel-1 (ผ่าน Microsoft Planetary Computer) · OCHA COD-AB ขอบเขตตำบล (CC BY-IGO) · Windy.com (embed) ·
FABDEM V1-2 (Hawker et al. 2022, **CC BY-NC-SA 4.0 — ใช้เชิงพาณิชย์ไม่ได้**; ถ้าจะใช้เชิงพาณิชย์ให้กลับไปใช้ Copernicus DEM) · OpenStreetMap (ODbL) ·
Copernicus DEM GLO-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the European Union and ESA ·
ESA WorldCover 2021 (CC BY 4.0) · geoBoundaries (CC BY 4.0) · แผนที่ฐาน © Esri (World Light Gray Canvas, World Imagery), © OpenStreetMap contributors
