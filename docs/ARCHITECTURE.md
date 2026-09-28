# Architecture & Model — Nakhon Sawan Flood Watch

## 1. เป้าหมายการออกแบบ

| ข้อกำหนด | การออกแบบ |
|---|---|
| วิเคราะห์ด้วย ArcGIS Pro | งานหนักเชิงภูมิประเทศ (DEM hydrology, HAND, sink, CN, hex routing) ทำใน Pro แล้ว export เป็นพารามิเตอร์ราย hex |
| ข้อมูลฝนจากหน่วยงาน/Open source | Open-Meteo (ฝนรายชั่วโมงต่อเนื่อง + พยากรณ์), ThaiWater (สถานีโทรมาตรจริง), GISTDA (ดาวเทียม), Windy (ภาพประกอบ) |
| พื้นที่ท่วมขัง + ระยะเวลา | โมเดลสมดุลน้ำรายชั่วโมงบน hex + routing ตามทิศการไหล → ความลึก, ชั่วโมงที่ท่วมแล้ว, ชั่วโมงที่เหลือ |
| Near-realtime | GitHub Actions ทุก 1 ชม. (ข้อมูลต้นทางอัปเดต 10–60 นาที) + หน้าเว็บ poll ทุก 5 นาที |
| Public, ต้นทุนต่ำ | Static site บน GitHub Pages, ไม่มีเซิร์ฟเวอร์/ฐานข้อมูล, ไม่ต้องใช้ license ArcGIS Online |

แยก **static (ช้า, แม่นเชิงพื้นที่)** ออกจาก **dynamic (เร็ว, เบา)**: ArcGIS Pro คำนวณสิ่งที่ไม่เปลี่ยนตามเวลาครั้งเดียว
pipeline รายชั่วโมงจึงเหลือแค่ numpy บน ~9,600 hex × ~820 ชั่วโมง (ใช้เวลา < 1 วินาทีสำหรับโมเดล)

## 2. ชั้นข้อมูลคงที่ (ArcGIS Pro — `arcgis/build_static.py`)

| ขั้น | เครื่องมือ | ผลลัพธ์ |
|---|---|---|
| DEM | Mosaic To New Raster → Project Raster (UTM 47N, 30 ม., bilinear) → Extract by Mask (จังหวัด + buffer 3 กม.) | `dem` |
| อุทกวิทยา | Fill → Flow Direction (D8) → Flow Accumulation | `fill`, `fdir`, `facc` |
| ลำน้ำ | Con(facc > 5 กม²), Con(facc > 1,000 กม²) | `stream`, `major` |
| HAND | Flow Distance (VERTICAL) ถึง `stream` / `major` | `hand`, `hand_major` |
| แอ่งกักเก็บ | Fill − DEM | `sink` |
| ความลาดชัน | Slope (percent rise) | `slope` |
| Curve Number | ESA WorldCover → Reclassify (HSG D ถ้า HAND < 5 ม. ไม่งั้น HSG C) | `cn` |
| Hex | Generate Tessellation 1 กม² (center in province), Spatial Join อำเภอ, Polygon to Raster | `hex`, `hexid` |
| Zonal | numpy (mean, P10, sum) ราย hex | `params.json` |
| Downstream | จาก cell ที่ facc สูงสุดใน hex เดินตาม D8 จนออกนอก hex | `params.down` |
| Overlay | HAND 4 ชั้น → Web Mercator 60 ม. → PNG | `susceptibility.png` |
| Station ref | min(fill) รัศมี 300 ม. รอบสถานีระดับน้ำ ThaiWater | `stations_ref.json` (`z_ref`) |

ค่า CN (HSG C / D): ป่า 70/77, ไม้พุ่ม 77/83, ทุ่งหญ้า 74/80, นา/เกษตร 82/86, เมือง 90/93, ดินโล่ง 86/89, น้ำ 98, พื้นที่ชุ่มน้ำ 85/88

## 3. แหล่งข้อมูล near-realtime

| แหล่ง | Endpoint | ความถี่ | ใช้ทำอะไร |
|---|---|---|---|
| Open-Meteo Forecast API | `api.open-meteo.com/v1/forecast?hourly=precipitation&past_days=30&forecast_days=4` (grid 0.1°, ~280 จุด, batch 80) | ~1 ชม. | ฝนรายชั่วโมงต่อเนื่องทั้งพื้นที่ (ECMWF/GFS best-match) |
| ThaiWater (สสน.) | `api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_24h` | 10–60 นาที | ฝน 24 ชม. จากสถานีโทรมาตร (HII, กรมอุตุฯ, ชป., ปภ., ทน.) → ปรับแก้ฝนแบบจำลอง |
| ThaiWater (สสน.) | `.../public/waterlevel` | 10–60 นาที | ระดับน้ำ ม.รทก., ตลิ่งต่ำสุด, แนวโน้ม, สถานการณ์ → น้ำล้นตลิ่ง + backwater |
| GISTDA Disaster API | `api-gateway.gistda.or.th/api/2.0/resources/features/flood/3days?pv_idn=60` (header `API-Key`) | รายวัน | พื้นที่น้ำท่วมจากดาวเทียม — แสดงเทียบ/สอบเทียบ |
| Windy | `embed.windy.com/embed.html` | – | ภาพเรดาร์/ฝน/เมฆประกอบ (Point Forecast API ของ Windy เป็นแบบเสียเงิน จึงไม่ใช้ในการคำนวณ) |
| กรมอุตุฯ TMD API | `data.tmd.go.th` (ต้องสมัคร uid/ukey) | – | ขยายต่อได้: เรดาร์/ฝนรายชั่วโมงสถานีหลัก |

## 4. โมเดล (`pipeline/model.py`)

ต่อ hex *i* ต่อชั่วโมง *t*

1. **ฝน**: IDW 4 จุด grid → P(t). ปรับแก้ 24 ชม. ล่าสุดด้วย ratio = (G+2)/(M+2) ที่สถานี (จำกัด 0.2–5), interpolate log-ratio ด้วย IDW, น้ำหนักเต็มภายใน 10 กม. ลดถึง 0 ที่ 40 กม.
2. **น้ำท่า SCS-CN** แบบเหตุการณ์ (เริ่มเหตุการณ์ใหม่เมื่อฝนหยุด ≥ 12 ชม.)
   S = 25400/CN − 254, Ia = 0.2S, Q = (P−Ia)²/(P−Ia+S); CN ปรับ AMC I/III จากฝน 5 วันก่อนเหตุการณ์ (35/53 มม.)
3. **กักเก็บ** W(t) (มม.) ; น้ำปกติในคันนา N = 100·f_นา + 10 ; D_cap = N + 0.3·min(sink_mm, 60)
4. **ระบาย** Out = max(W − D_cap, 0)·(1 − e^(−1/T)) + 0.5·max(W − D_cap − 800, 0), T = clip(8/√slope%, 2, 96) ชม. × (1 − 0.3·f_เมือง)
5. **ร่องน้ำ** น้ำจากต้นน้ำผ่านร่องได้ Qc = 7.2·A^0.75·clip(√slope%, 0.5, 4) มม./ชม. (≈ bankfull 2·A^0.75 ม³/วิ, A = พื้นที่รับน้ำ กม²) ส่วนเกินล้นเข้าพื้นที่
6. **backwater** ถ้าสถานีใกล้เคียง (≤15 กม.) ระดับน้ำ > ตลิ่ง − 1 ม. และ HAND_major < 6 ม. → T×3, Qc/3
7. **สูญเสีย** 0.15–0.3 มม./ชม. เมื่อฝน < 2 มม./ชม. (ระเหย + ซึม)
8. **ความลึก** d = max(W − N, 0) / f_spread ; f_spread = clip(0.7·f_low + 0.15, 0.2, 0.85) ; hex ที่เป็นแหล่งน้ำถาวร (>50%) ไม่นับ
9. **น้ำล้นตลิ่ง** เมื่อ WL > ตลิ่ง: d_r = clip((WL − z_ref) − HAND_major_P10, 0, WL − ตลิ่ง)
10. d = max(d_ฝน, d_ล้นตลิ่ง) → ชั้น: <10 ไม่ท่วม, 10–25 เฝ้าระวัง, 25–50 ท่วมขังเล็กน้อย, 50–100 ปานกลาง, >100 ซม. ท่วมสูง
11. **ระยะเวลา**: ชั่วโมงต่อเนื่องที่ d ≥ 10 ซม. ถึงปัจจุบัน; เวลาที่เหลือ = ชั่วโมงแรกในอนาคตที่ d < 10 ซม. (ถ้าเกิน 72 ชม. ใช้อัตราการลด 12 ชม. สุดท้าย, ล้นตลิ่งใช้แนวโน้มระดับน้ำ), เพดาน 14 วัน

จำลองต่อเนื่องย้อนหลัง 30 วันทุกครั้ง (cold start แห้ง) จึงไม่ต้องเก็บ state ระหว่างรอบ

### ค่าเริ่มต้นและการทดสอบความไว (28 ก.ย. 2569)
พารามิเตอร์ข้างต้นตั้งจากหลักอุทกวิทยาและทดสอบกับฝนจริง (ฝนเฉลี่ย 7 วัน ≈ 134 มม.): ได้พื้นที่เฝ้าระวัง ~400 กม² ;
ฝน ×1.5 → ~1,300 กม², ×2 → ~2,300 กม² (โมเดลตอบสนองแบบไม่เชิงเส้นตามที่คาด) — **ยังไม่ได้สอบเทียบกับพื้นที่ท่วมจริง** ดูข้อ 6.1

## 5. สัญญาข้อมูล (data contract)

- `params.json` — array ยาว N เรียงตาม `hid`: `lon, lat, amph, elev, hand, hand_p10, handM_p10, slope, cn, sink_mm, f_low, f_crop, f_built, f_water, facc_km2, down`
- `status.json` — array ยาว N: `d` (ซม.), `c` (ชั้น), `s` (1 ฝน, 2 ล้นตลิ่ง, 3 ทั้งคู่), `h`, `r` (ชม., 999 = >14 วัน), `p24, p72, p7d, f24, f72` (มม.), `m72, dm72`
- `frames.json` — `t` (unix), `now`, `c` (string ของเลขชั้นยาว N ต่อเฟรม ทุก 3 ชม.)
- `districts.json`, `stations.json`, `series.json`, `meta.json` (เวลา, สถานะแหล่งข้อมูล, สรุป)

## 6. แนวทางยกระดับ

1. **สอบเทียบ** พารามิเตอร์ (T, D_cap, f_spread) กับพื้นที่ท่วม GISTDA ปี 2554/2565/2567 — Pro: Raster Calculator + confusion matrix ราย hex
2. **DEM ละเอียด** DEM 5 ม. พด./LiDAR หรือ FABDEM + burn คันกั้นน้ำ/ถนนจาก OSM
3. **ฝนเรดาร์/ดาวเทียม** GSMaP_NOW, GPM IMERG Early หรือเรดาร์กรมอุตุฯ แทนฝนแบบจำลองช่วงย้อนหลัง
4. **น้ำไหลเข้าจากต้นน้ำ** ใช้ปริมาณน้ำ C.2/P.17/N.67 + การระบายเขื่อนภูมิพล/สิริกิติ์ (ThaiWater `thailand_main`) พยากรณ์ระดับน้ำล่วงหน้า
5. **ผลกระทบ** ซ้อนประชากร (WorldPop), ถนน, โรงพยาบาล/โรงเรียน → รายงานผู้ได้รับผลกระทบรายตำบล
6. **ArcGIS Online/Enterprise** ถ้าต้องการ: publish `hex_status` เป็น hosted feature layer แล้วใช้ Experience Builder/Dashboards (pipeline เดิมใช้ต่อได้ แค่เพิ่มขั้น `arcgis` Python API `edit_features`)
