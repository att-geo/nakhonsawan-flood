# -*- coding: utf-8 -*-
"""
Rapid ponding model (screening level) สำหรับ hex grid 1 km²

ต่อ hex ต่อชั่วโมง:
  1) น้ำท่าจากฝน  : SCS-CN แบบต่อเนื่องตามเหตุการณ์ฝน (event resets หลังฝนหยุด 12 ชม.)
                   ปรับ AMC I/II/III จากฝนสะสม 5 วันก่อนเริ่มเหตุการณ์
  2) กักเก็บ       : W (mm) = น้ำในพื้นที่ hex, D_cap = คันนา (100 มม.×สัดส่วนนา) + 0.3·แอ่ง DEM
  3) ระบายออก      : ส่วนที่เกิน D_cap ไหลออกแบบ linear reservoir (T จากความลาดชัน)
                   ไปยัง hex ท้ายน้ำ (downstream จาก ArcGIS Pro) ในชั่วโมงถัดไป
  4) ร่องน้ำ       : น้ำจากต้นน้ำผ่านร่องน้ำได้ไม่เกิน Qc = 7.2·A^0.75·√S ส่วนเกินล้นเข้าพื้นที่
  5) สูญเสีย       : ระเหย + ซึม
  6) ความลึก      : ส่วนเกินน้ำปกติในนา กระจายบนสัดส่วนพื้นที่ต่ำ (HAND < 2 ม.)
น้ำล้นตลิ่งจากแม่น้ำ (riverine) คำนวณจากระดับน้ำจริงเทียบ HAND ของแม่น้ำสายหลัก
"""
import numpy as np

CLASS_CM = (10, 25, 50, 100)         # ขอบชั้นความลึก (ซม.) -> class 0..4
FLOOD_CM = 10                        # เกณฑ์นับว่า "ท่วมขัง" สำหรับระยะเวลา


def depth_class(d_cm):
    return np.digitize(d_cm, CLASS_CM).astype(np.int8)


def _cn_amc(cn2, p5):
    cn1 = cn2 / (2.281 - 0.01281 * cn2)
    cn3 = cn2 / (0.427 + 0.00573 * cn2)
    w = np.clip((p5 - 35.0) / (53.0 - 35.0), 0, 1)          # growing-season thresholds
    lo = cn1 + (cn2 - cn1) * np.clip(p5 / 35.0, 0, 1)
    return np.where(p5 < 35, lo, cn2 + (cn3 - cn2) * w)


class Params:
    def __init__(self, p):
        g = lambda k, d: np.where(np.asarray(p[k], float) < 0, d, np.asarray(p[k], float))
        self.n = p["n"]
        self.cn = np.clip(g("cn", 82), 40, 98)
        self.slope = g("slope", 1.0)
        self.sink = np.clip(g("sink_mm", 10), 0, 150)
        self.f_crop = g("f_crop", 0.5)
        self.f_built = g("f_built", 0.0)
        self.f_low = g("f_low", 0.2)
        self.facc = np.maximum(g("facc_km2", 1), 0.05)
        self.handM = np.asarray(p.get("handM_p10", [-1] * self.n), float)
        self.hand = g("hand", 5)
        self.down = np.asarray(p["down"], int)
        self.lon = np.asarray(p["lon"], float)
        self.lat = np.asarray(p["lat"], float)
        self.f_water = g("f_water", 0.0)
        # derived (calibrated for rainy-season paddy plains; ดู docs/ARCHITECTURE.md)
        self.normal = 100 * self.f_crop + 10                            # mm น้ำในคันนา/แอ่งปกติ (ไม่ถือเป็นน้ำท่วม)
        self.dcap = self.normal + 0.3 * np.minimum(self.sink, 60)       # mm ความจุที่ไม่ไหลออก (คันนา + แอ่ง DEM)
        self.tres = np.clip(8 / np.sqrt(np.maximum(self.slope, 0.05)), 2, 96) * (1 - 0.3 * self.f_built)
        # ความจุร่องน้ำ ~ bankfull Q = 2·A^0.75 m3/s (1 m3/s = 3.6 mm/h บน 1 กม²) ปรับตาม sqrt(slope)
        self.qc = 7.2 * self.facc ** 0.75 * np.clip(np.sqrt(self.slope), 0.5, 4)
        self.loss = 0.3 - 0.15 * self.f_built                           # mm/h ระเหย + ซึม
        self.spread = np.clip(0.7 * self.f_low + 0.15, 0.2, 0.85)       # สัดส่วนพื้นที่ที่น้ำไปกอง
        self.surcharge = 800.0                                          # mm เกินจากนี้ล้นกระจายออกเร็ว
        self.is_water = self.f_water > 0.5                              # แหล่งน้ำถาวร (ไม่นับเป็นน้ำท่วม)


def simulate(P, prm, backwater=None):
    """P: array [T, N] ฝนรายชั่วโมง (mm). คืน depth_cm [T, N] (น้ำท่วมขังจากฝน)"""
    T, N = P.shape
    tres = prm.tres.copy(); qc = prm.qc.copy()
    if backwater is not None:
        tres = np.where(backwater, tres * 3, tres)
        qc = np.where(backwater, qc / 3, qc)
    kout = 1 - np.exp(-1.0 / tres)
    has_down = prm.down >= 0
    dn = np.where(has_down, prm.down, 0)

    W = np.zeros(N)                 # storage mm
    inflow = np.zeros(N)            # from upstream (arrives this hour)
    pcum = np.zeros(N)              # event rainfall
    qcum = np.zeros(N)              # event runoff
    dry = np.full(N, 99.0)          # hours since last rain
    cn_ev = prm.cn.copy()
    S = 25400 / cn_ev - 254
    out = np.zeros((T, N), np.float32)
    csum = np.cumsum(np.vstack([np.zeros((1, N)), P]), axis=0)

    for t in range(T):
        p = P[t]
        # --- event bookkeeping / AMC
        start = (p > 0.1) & (dry >= 12)
        if start.any():
            p5 = csum[t] - csum[max(t - 120, 0)]
            cn_ev = np.where(start, _cn_amc(prm.cn, p5), cn_ev)
            S = np.where(start, 25400 / cn_ev - 254, S)
            pcum = np.where(start, 0, pcum); qcum = np.where(start, 0, qcum)
        dry = np.where(p > 0.1, 0, dry + 1)
        # --- SCS-CN incremental runoff
        pcum = pcum + p
        ia = 0.2 * S
        qnew = np.where(pcum > ia, (pcum - ia) ** 2 / (pcum - ia + S), 0.0)
        q = np.maximum(qnew - qcum, 0); qcum = qnew
        # --- channel pass-through vs overbank
        through = np.minimum(inflow, qc)
        over = inflow - through
        W = W + q + over
        # --- drainage of storage above depression capacity
        drain = np.maximum(W - prm.dcap, 0) * kout + 0.5 * np.maximum(W - prm.dcap - prm.surcharge, 0)
        W = W - drain
        W = np.maximum(W - np.where(p > 2, 0, prm.loss), 0)
        # --- send to downstream (next hour)
        send = through + drain
        inflow = np.zeros(N)
        np.add.at(inflow, dn[has_down], send[has_down])
        out[t] = np.where(prm.is_water, 0, np.maximum(W - prm.normal, 0) / prm.spread / 10.0)   # cm
    return out


def river_depth(prm, stations, max_km=15.0):
    """ความลึกน้ำล้นตลิ่ง (ซม.) จากสถานีระดับน้ำ: d = clip((WL - z_ref) - HAND_major, 0, WL - bank)
    stations: list of dict(lat, lon, wl, bank, z_ref)"""
    N = prm.n
    d = np.zeros(N); bw = np.zeros(N, bool); nearest = np.full(N, -1)
    if not stations:
        return d, bw, nearest
    lat = np.array([s["lat"] for s in stations]); lon = np.array([s["lon"] for s in stations])
    kx = 111.32 * np.cos(np.radians(15.7))
    dist = np.hypot((prm.lon[:, None] - lon[None]) * kx, (prm.lat[:, None] - lat[None]) * 110.57)
    j = dist.argmin(1); dj = dist[np.arange(N), j]
    ok = (dj <= max_km) & (prm.handM >= 0)
    nearest = np.where(dj <= max_km, j, -1)
    for k, s in enumerate(stations):
        sel = ok & (j == k)
        e = s["wl"] - s["bank"]
        if e > -1.0:                                      # ใกล้ตลิ่ง -> ระบายช้า (backwater)
            bw |= sel & (prm.handM < 6)
        if e > 0 and s.get("z_ref") is not None:
            dd = np.clip((s["wl"] - s["z_ref"]) - prm.handM, 0, e)
            d = np.where(sel & ~prm.is_water, np.maximum(d, dd * 100), d)
    return d, bw, nearest


def durations(depth, i_now, thr=FLOOD_CM, cap=336):
    """ชั่วโมงที่ท่วมต่อเนื่องมาแล้ว และคาดว่าจะเหลืออีกกี่ชั่วโมง (999 = เกิน cap)"""
    T, N = depth.shape
    wet = depth >= thr
    hrs = np.zeros(N, int)
    alive = wet[i_now].copy()
    for t in range(i_now, -1, -1):
        alive &= wet[t]
        hrs += alive
        if not alive.any():
            break
    rem = np.full(N, -1)
    fut = wet[i_now:]
    now_wet = wet[i_now]
    dry_idx = np.where(fut.any(0) & (~fut).any(0), np.argmax(~fut, 0), -1)
    rem = np.where(now_wet & (dry_idx > 0), dry_idx, rem)
    still = now_wet & fut.all(0)
    if still.any():
        tail = depth[-1] - depth[max(T - 13, 0)]
        rate = np.where(tail < 0, -tail / 12.0, 0)
        extra = np.where(rate > 0, (depth[-1] - thr) / np.maximum(rate, 1e-6), cap)
        rem = np.where(still, np.minimum((T - 1 - i_now) + extra, cap), rem)
    rem = np.where(rem >= cap, 999, rem)
    return hrs, rem.astype(int)
