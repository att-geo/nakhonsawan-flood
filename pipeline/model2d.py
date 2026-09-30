# -*- coding: utf-8 -*-
"""
แบบจำลองน้ำท่วม 2 มิติ (rain-on-grid, local inertial — Bates et al. 2010 / de Almeida et al. 2012)
สำหรับจุดวิกฤต ความละเอียด ~90 ม. — numpy ล้วน รันได้ใน GitHub Actions และ ArcGIS Pro

สมการ (ต่อหน้า cell):  q ← (q − g·h_f·Δt·∂η/∂x) / (1 + g·Δt·n²·|q| / h_f^(7/3))
                         ∂h/∂t = −∇·q + ฝนส่วนที่เป็นน้ำท่า (SCS-CN) − การซึม/ระเหย
เงื่อนไขขอบ: ขอบโดเมนเปิด (ไหลออกได้), cell แม่น้ำสายหลักกำหนดระดับน้ำจากสถานี (stage boundary)
เงื่อนไขเริ่มต้น: ระดับผิวน้ำราย hex จากโมเดล hex network ณ เวลาเริ่ม (ต่อยอดสภาพน้ำขังที่มีอยู่แล้ว)
"""
import json, os, struct, zlib
import numpy as np

G = 9.81


def load_domain(path):
    d = np.load(path)
    meta = json.loads(str(d["meta"]))
    return {k: d[k] for k in d.files if k != "meta"}, meta


def run2d(dom, meta, rain_eff, t_hours, stage_fn, h0=None, alpha=0.7, hcap=3.0, loss_mmh=0.3,
          snap_hours=(), stats_every=1, river_bank=None, overflow_fn=None,
          dur_from=None, wet_thr=0.10, zones=None, n_zones=0, hold_mask=None, hold_level=None):
    """rain_eff: [T, ny, nx] มม./ชม. (น้ำท่าจากฝน) หรือ [T] ค่าเดียวทั้งโดเมน — ชั่วโมงที่เกินความยาว = ไม่มีฝน
    dur_from: ชั่วโมงเริ่มนับระยะเวลาท่วมขัง (h ≥ wet_thr) ราย cell -> wet_h (ชม.ที่ท่วม), last_wet (ชม.สุดท้ายที่ยังท่วม, -1 = ไม่ท่วม)
    zones/n_zones: index พื้นที่ (เช่น ตำบล, -1 = นอก) -> zseries พื้นที่ท่วมรายชั่วโมงราย zone (กม²)
    hold_mask/hold_level: แอ่งที่มีคัน/ประตูน้ำกั้น (เช่น บึงบอระเพ็ด) — น้ำไหลออกข้ามขอบแอ่งไม่ได้เมื่อระดับน้ำในแอ่งต่ำกว่า hold_level
                          (ม.รทก. ; ค่าเดียว หรือ ฟังก์ชันของชั่วโมง) ; น้ำไหลเข้าแอ่งได้ตามปกติ
    stage_fn(t_hour) -> ndarray ระดับผิวน้ำของ cell แม่น้ำ (ลำดับตาม np.where(river>=0)) หรือ dict station_index -> ระดับน้ำ (ม.รทก.)
    คืน dict: hmax, snaps{hour: h}, series (พื้นที่ท่วม/ปริมาตรรายชั่วโมง)"""
    f4 = np.float32
    z = dom["dem"].astype(f4); n = dom["n"].astype(f4)
    valid = dom["valid"].astype(bool); river = dom["river"].astype(np.int16)
    ny, nx = z.shape; dx = float(meta["dx"])
    z = np.where(valid, z, np.nanmax(z[valid]) + 50)                   # นอกโดเมน = สูง (กำแพง)
    h = np.zeros_like(z) if h0 is None else np.where(valid, h0, 0).astype(f4)
    qx = np.zeros((ny, nx + 1), f4); qy = np.zeros((ny + 1, nx), f4)
    zmx = np.maximum(z[:, :-1], z[:, 1:]); zmy = np.maximum(z[:-1, :], z[1:, :])
    nnx = (0.5 * (n[:, :-1] ** 2 + n[:, 1:] ** 2)).astype(f4); nny = (0.5 * (n[:-1, :] ** 2 + n[1:, :] ** 2)).astype(f4)
    riv = river >= 0
    # ตลิ่ง: ถ้าระดับน้ำในแม่น้ำต่ำกว่าตลิ่ง ห้ามน้ำไหลจาก cell แม่น้ำขึ้นที่ราบ
    # (ตลิ่ง/คันดินแคบกว่าขนาด cell จึงหายไปเมื่อ resample DEM เป็น 120 ม.) — ที่ราบยังระบายลงแม่น้ำได้ตามปกติ
    riv_xl = riv[:, :-1] & ~riv[:, 1:]; riv_xr = ~riv[:, :-1] & riv[:, 1:]
    riv_yt = riv[:-1, :] & ~riv[1:, :]; riv_yb = ~riv[:-1, :] & riv[1:, :]
    over = np.ones_like(riv)
    if hold_mask is not None:
        hm_ = hold_mask.astype(bool)
        hxl = hm_[:, :-1] & ~hm_[:, 1:]; hxr = ~hm_[:, :-1] & hm_[:, 1:]      # แอ่งอยู่ซ้าย / ขวา ของหน้า x
        hyt = hm_[:-1, :] & ~hm_[1:, :]; hyb = ~hm_[:-1, :] & hm_[1:, :]
    hl_ = None
    hmax = h.copy(); snaps = {}; series = []
    n2 = n * n
    t = 0.0; T_end = float(t_hours); hour = -1; ovl = None
    track = dur_from is not None
    wet_h = np.zeros_like(z) if track else None
    last_wet = np.full(z.shape, -1.0, f4) if track else None
    zs = zones.astype(np.int32) if zones is not None else None
    zok = (zs >= 0) & valid & ~riv if zs is not None else None
    zseries = []
    while t < T_end - 1e-6:
        # --- hourly forcing
        if int(t) != hour:
            hour = int(t)
            st = stage_fn(hour)
            ovl = overflow_fn(hour) if overflow_fn else None        # ลบ.ม./วิ ที่ล้นตลิ่งได้จริง (Q - ความจุลำน้ำ)
            if hold_mask is not None:
                hl_ = hold_level(hour) if callable(hold_level) else hold_level
            ra = rain_eff[hour] if hour < len(rain_eff) else 0.0
            r_ms = ((ra if np.ndim(ra) else np.full_like(z, ra)) / 1000.0 / 3600.0).astype(f4)
            if stats_every and hour % stats_every == 0:
                wet = (h >= 0.10) & valid & ~riv
                series.append([hour, float(wet.sum() * dx * dx / 1e6), float((h * valid).sum() * dx * dx / 1e6)])
                if zs is not None:
                    wz = wet & zok
                    zseries.append(np.bincount(zs[wz], minlength=n_zones)[:n_zones] * dx * dx / 1e6)
            if hour in snap_hours:
                snaps[hour] = h.astype(np.float32).copy()
        # river stage boundary
        if riv.any():
            if isinstance(st, np.ndarray):           # ระดับผิวน้ำราย cell แม่น้ำ (interpolate ระหว่างสถานีแล้ว)
                h[riv] = np.maximum(st - z[riv], 0.0)
                if river_bank is not None:
                    over = np.zeros_like(riv); over[riv] = st > river_bank
            else:
                for k, wl in st.items():             # ระดับผิวน้ำ = ระดับน้ำสถานีที่ใกล้ที่สุด
                    m = river == k
                    h[m] = np.maximum(wl - z[m], 0.0)
        eta = z + h
        hc = np.minimum(np.where(valid & ~riv, h, np.minimum(h, hcap)), hcap)
        dt = alpha * dx / np.sqrt(G * max(float(hc.max()), 0.05))
        dt = min(dt, 60.0, (T_end - t) * 3600.0)
        # --- x faces (interior)
        e1, e2 = eta[:, :-1], eta[:, 1:]
        hf = np.minimum(np.maximum(e1, e2) - zmx, hcap); nn = nnx
        q = qx[:, 1:-1]
        wetf = hf > 1e-3
        qn = (q - G * np.where(wetf, hf, 0) * dt * (e2 - e1) / dx) / (1 + G * dt * nn * np.abs(q) / np.where(wetf, hf, 1) ** (7 / 3))
        qx[:, 1:-1] = np.where(wetf, qn, 0)
        if river_bank is not None:
            q = qx[:, 1:-1]
            b1 = riv_xl & ~over[:, :-1]; q[b1] = np.minimum(q[b1], 0)       # แม่น้ำ(ซ้าย) -> ที่ราบ(ขวา) ปิด
            b2 = riv_xr & ~over[:, 1:]; q[b2] = np.maximum(q[b2], 0)        # แม่น้ำ(ขวา) -> ที่ราบ(ซ้าย) ปิด
        if hl_ is not None:
            q = qx[:, 1:-1]
            c1 = hxl & (eta[:, :-1] < hl_); q[c1] = np.minimum(q[c1], 0)    # แอ่ง(ซ้าย) -> นอก ปิดเมื่อต่ำกว่าระดับเก็บกัก
            c2 = hxr & (eta[:, 1:] < hl_); q[c2] = np.maximum(q[c2], 0)
        # --- y faces
        e1, e2 = eta[:-1, :], eta[1:, :]
        hf = np.minimum(np.maximum(e1, e2) - zmy, hcap); nn = nny
        q = qy[1:-1, :]
        wetf = hf > 1e-3
        qn = (q - G * np.where(wetf, hf, 0) * dt * (e2 - e1) / dx) / (1 + G * dt * nn * np.abs(q) / np.where(wetf, hf, 1) ** (7 / 3))
        qy[1:-1, :] = np.where(wetf, qn, 0)
        if river_bank is not None:
            q = qy[1:-1, :]
            b1 = riv_yt & ~over[:-1, :]; q[b1] = np.minimum(q[b1], 0)
            b2 = riv_yb & ~over[1:, :]; q[b2] = np.maximum(q[b2], 0)
        if hl_ is not None:
            q = qy[1:-1, :]
            c1 = hyt & (eta[:-1, :] < hl_); q[c1] = np.minimum(q[c1], 0)
            c2 = hyb & (eta[1:, :] < hl_); q[c2] = np.maximum(q[c2], 0)
        if ovl is not None and riv.any():
            # อนุรักษ์มวล: น้ำจากแม่น้ำขึ้นที่ราบรวมกันไม่เกินปริมาณที่เกินความจุลำน้ำ (ขอบเขตระดับน้ำไม่ใช่แหล่งน้ำไม่จำกัด)
            qxi = qx[:, 1:-1]; qyi = qy[1:-1, :]
            fx1 = riv_xl & (qxi > 0); fx2 = riv_xr & (qxi < 0); fy1 = riv_yt & (qyi > 0); fy2 = riv_yb & (qyi < 0)
            F = (qxi[fx1].sum() - qxi[fx2].sum() + qyi[fy1].sum() - qyi[fy2].sum()) * dx
            if F > ovl:
                k_ = ovl / F
                for arr, msk in ((qxi, fx1), (qxi, fx2), (qyi, fy1), (qyi, fy2)):
                    arr[msk] *= k_
        # --- open boundary on domain edge: normal-depth outflow (slope 1e-3)
        hb = np.maximum(h, 0)
        ob = lambda hh, nb: hh ** (5 / 3) * np.sqrt(1e-3) / np.sqrt(nb)
        qx[:, 0] = -ob(hb[:, 0], n2[:, 0]) * valid[:, 0]; qx[:, -1] = ob(hb[:, -1], n2[:, -1]) * valid[:, -1]
        qy[0, :] = -ob(hb[0, :], n2[0, :]) * valid[0, :]; qy[-1, :] = ob(hb[-1, :], n2[-1, :]) * valid[-1, :]
        # --- limit outflow to available water (positivity)
        outx = np.maximum(qx[:, 1:], 0) + np.maximum(-qx[:, :-1], 0)
        outy = np.maximum(qy[1:, :], 0) + np.maximum(-qy[:-1, :], 0)
        tot_out = (outx + outy) * dt / dx
        scale = np.where(tot_out > h, h / np.maximum(tot_out, 1e-12), 1.0)
        qx[:, 1:] = np.where(qx[:, 1:] > 0, qx[:, 1:] * scale, qx[:, 1:])
        qx[:, :-1] = np.where(qx[:, :-1] < 0, qx[:, :-1] * scale, qx[:, :-1])
        qy[1:, :] = np.where(qy[1:, :] > 0, qy[1:, :] * scale, qy[1:, :])
        qy[:-1, :] = np.where(qy[:-1, :] < 0, qy[:-1, :] * scale, qy[:-1, :])
        # --- continuity
        dh = dt / dx * (qx[:, :-1] - qx[:, 1:] + qy[:-1, :] - qy[1:, :]) + r_ms * dt
        h = np.maximum(h + dh, 0)
        h = np.where(valid, np.maximum(h - np.where(r_ms > 0, 0, loss_mmh / 1000 / 3600 * dt), 0), 0)
        hmax = np.maximum(hmax, np.where(riv, 0, h))
        t += dt / 3600.0
        if track and t > dur_from:
            w_ = (h >= wet_thr) & valid & ~riv
            wet_h += np.where(w_, dt / 3600.0, 0).astype(f4)
            last_wet = np.where(w_, np.float32(t), last_wet)
    hour = int(round(t))
    if hour in snap_hours:
        snaps[hour] = h.astype(np.float32).copy()
    out = {"hmax": hmax.astype(np.float32), "h": h.astype(np.float32), "snaps": snaps, "series": series}
    if track:
        out["wet_h"] = wet_h; out["last_wet"] = last_wet
    if zs is not None:
        out["zseries"] = np.array(zseries, np.float32)
    return out


# ------------------------------------------------------------------ tiny PNG writer (no Pillow)
def write_png(path, rgba):
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[r].tobytes() for r in range(h))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    open(path, "wb").write(png)


DEPTH_BR = (0.10, 0.25, 0.50, 1.00)
DEPTH_COL = [(0, 0, 0, 0), (191, 219, 254, 200), (96, 165, 250, 215), (37, 99, 235, 230), (30, 58, 138, 240)]


# ระยะเวลาที่คาดว่าน้ำขังจะลด (ชม. นับจากตอนนี้) — สีเดียวกับ REM_COL ในหน้าเว็บ
REM_BR = (24, 72, 168, 336)
REM_COL = [(134, 239, 172, 210), (253, 224, 71, 220), (251, 146, 60, 230), (220, 38, 38, 235), (127, 29, 29, 245)]


def remain_png(path, rem_h, still_wet, river=None):
    """rem_h: ชม.จากตอนนี้ถึงครั้งสุดท้ายที่ลึก ≥ 10 ซม. (< 0 = ไม่ท่วม) ; still_wet: ยังท่วมเมื่อจบการจำลอง -> ชั้น > 14 วัน"""
    k = np.digitize(np.maximum(rem_h, 0), REM_BR)
    k = np.where(still_wet, len(REM_BR), k)
    rgba = np.zeros(rem_h.shape + (4,), np.uint8)
    for i, col in enumerate(REM_COL):
        rgba[(k == i) & (rem_h >= 0)] = col
    if river is not None:
        rgba[river >= 0] = (0, 0, 0, 0)
    write_png(path, rgba)


def depth_png(path, h, river=None):
    k = np.digitize(h, DEPTH_BR)
    rgba = np.zeros(h.shape + (4,), np.uint8)
    for i, col in enumerate(DEPTH_COL):
        rgba[k == i] = col
    if river is not None:
        rgba[river >= 0] = (0, 0, 0, 0)
    write_png(path, rgba)
