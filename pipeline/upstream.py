# -*- coding: utf-8 -*-
"""
น้ำหลากจาก 4 จังหวัดต้นน้ำ (กำแพงเพชร พิจิตร พิษณุโลก เพชรบูรณ์) -> นครสวรรค์

1) Rainfall-runoff ราย zone (SCS-CN ต่อเนื่อง) -> หน่วงเวลา lag (ระยะทางไหล / 0.7 ม./วิ)
   + linear reservoir K = max(6, 0.5·lag) ชม. -> hydrograph ที่ entry hex (ลบ.ม./วิ)
2) Gauge assimilation: แม่น้ำสายหลัก (ปิง P.17, น่าน-ยม N.67, แม่วงก์ Ct.5A, เจ้าพระยา C.2)
   Q_fc(t) = Q_obs(now) + [Q_model(t) - Q_model(now)]   (C.2 รวม Δปิง + Δน่าน หน่วง 12 ชม.)
3) Floodplain level-pool: น้ำส่วนเกินความจุลำน้ำ (qmax ของ RID) สะสมเป็นปริมาตร S(t)
   กระจายลงที่ราบลุ่มของ reach ตาม HAND hypsometry (P5..P75) -> ระดับ h(t) -> ความลึกราย hex
4) ลำน้ำสายย่อยจาก 4 จังหวัด (ไม่มีสถานี) -> ฉีดเข้า hex cascade ของโมเดลน้ำท่วมขังที่ entry hex
"""
import numpy as np

UP_NAMES = {62: "กำแพงเพชร", 65: "พิษณุโลก", 66: "พิจิตร", 67: "เพชรบูรณ์"}
# reach -> (ชื่อ, สถานีหลักที่ใช้ Q, สถานีในช่วงลำน้ำ (ใช้กำหนดพื้นที่ที่ราบลุ่ม), แม่น้ำจาก entries)
REACHES = {
    "ping": ("แม่น้ำปิง", "P.17", ["P.17", "PIN005", "PIN004", "P.16"], ["แม่น้ำปิง"]),
    "nan": ("แม่น้ำน่าน-ยม", "N.67", ["N.67", "NAN008", "Y.5", "YOM009"], ["แม่น้ำน่าน", "แม่น้ำยม"]),
    "cpy": ("แม่น้ำเจ้าพระยา", "C.2", ["C.2", "CPY001", "CPY002"], []),
    "maewong": ("แม่วงก์-สะแกกรัง", "Ct.5A", ["Ct.5A", "Ct.4", "SKG003", "SKG005", "SKG004", "SKG007"], ["แม่น้ำแม่วงก์"]),
}
CALIB_GROUPS = {"ping": ([62], "P.17"), "nan": ([65, 66, 67], "N.67")}
UP_GAUGES = ["P.7A", "P.15", "P.16", "P.78", "N.5A", "N.7A", "Y.16", "Y.64", "Y.17", "Y.5", "N.24A", "N.54", "N.73"]
KX, KY = 107.1, 110.6          # km per degree at ~15.8°N


def scs_runoff(P, cn2, bucket=0.0, loss=0.25):
    """SCS-CN ต่อเนื่องแบบ event (reset หลังแล้ง 12 ชม., AMC จากฝน 5 วัน) -> q [T,N] mm/h
    bucket: ความจุกักเก็บผิวดิน (คันนา/แอ่ง, มม.) ที่ต้องเต็มก่อนเกิดน้ำท่า, ลดลง loss มม./ชม. เมื่อไม่มีฝน"""
    from model import _cn_amc
    T, N = P.shape
    pcum = np.zeros(N); qcum = np.zeros(N); dry = np.full(N, 99.0)
    cn_ev = cn2.copy(); S = 25400 / cn_ev - 254
    q = np.zeros((T, N), np.float32); B = np.zeros(N)
    csum = np.cumsum(np.vstack([np.zeros((1, N)), P]), axis=0)
    for t in range(T):
        p = P[t]
        start = (p > 0.1) & (dry >= 12)
        if start.any():
            cn_ev = np.where(start, _cn_amc(cn2, csum[t] - csum[max(t - 120, 0)]), cn_ev)
            S = np.where(start, 25400 / cn_ev - 254, S)
            pcum = np.where(start, 0, pcum); qcum = np.where(start, 0, qcum)
        dry = np.where(p > 0.1, 0, dry + 1)
        pcum = pcum + p; ia = 0.2 * S
        qn = np.where(pcum > ia, (pcum - ia) ** 2 / (pcum - ia + S), 0.0)
        qi = np.maximum(qn - qcum, 0); qcum = qn
        if bucket > 0:
            over = np.maximum(qi - (bucket - B), 0)
            B = np.maximum(B + qi - over - np.where(p > 2, 0, loss), 0)
            q[t] = over
        else:
            q[t] = qi
    return q


def route(qz, area, lag, T):
    """qz [T,Z] mm/h -> Q [T,Z] m3/s ที่จุดเข้า (lag + linear reservoir, FFT convolution ต่อกลุ่ม kernel)"""
    Qin = qz * area[None] / 3.6                       # mm/h * km2 -> m3/s
    L = np.round(lag).astype(int); K = np.maximum(6.0, 0.5 * lag).round().astype(int)
    out = np.zeros_like(Qin, dtype=float)
    nfft = 1 << int(np.ceil(np.log2(2 * T)))
    for key in np.unique(L * 1000 + K):
        l, k = divmod(int(key), 1000)
        sel = np.where((L == l) & (K == k))[0]
        n = min(T, l + 6 * k)
        ker = np.zeros(n); tt = np.arange(n) - l
        ker[tt >= 0] = np.exp(-tt[tt >= 0] / k)
        if ker.sum() == 0:
            continue
        ker /= ker.sum()
        fk = np.fft.rfft(ker, nfft)
        out[:, sel] = np.fft.irfft(np.fft.rfft(Qin[:, sel], nfft, axis=0) * fk[:, None], nfft, axis=0)[:T]
    return np.maximum(out, 0)


class Upstream:
    def __init__(self, zones, entries, prm):
        self.z = {k: (np.asarray(v) if isinstance(v, list) else v) for k, v in zones.items()}
        self.entries = entries["entries"]
        self.meta = entries
        self.prm = prm
        self.ent_hex = np.array([e["hex"] for e in self.entries])
        self.ent_river = {e["hex"]: e.get("river", "") for e in self.entries}

    # ---- zone rainfall-runoff -> entries / provinces
    def run(self, Pz, bucket=50.0):
        """คืน Q ราย zone [T,Z] (ลบ.ม./วิ ที่จุดเข้า) และน้ำท่าที่เกิด [T,Z] (ลบ.ม./ชม.)"""
        z = self.z; T = Pz.shape[0]
        q = scs_runoff(Pz, z["cn"].astype(float), bucket=bucket)
        Qz = route(q, z["area_km2"].astype(float), z["lag_h"].astype(float), T)
        gen = q * z["area_km2"][None] * 1000.0
        return Qz, gen

    def aggregate(self, Qz, gen, scale):
        z = self.z; T = Qz.shape[0]
        Qs = Qz * scale[None]
        uent, einv = np.unique(z["entry"], return_inverse=True)
        Qe = np.zeros((T, uent.size)); np.add.at(Qe.T, einv, Qs.T)
        pc = z["pcode"]
        Qp = {c: Qs[:, pc == c].sum(1) for c in UP_NAMES}
        Gp = {c: (gen[:, pc == c] * scale[pc == c][None]).sum(1) for c in UP_NAMES}
        return uent, Qe, Qp, Gp

    def calibrate(self, Qz, i_now, gauges, beta=0.6, kmin=0.05):
        """ปรับขนาดน้ำท่าแบบจำลองด้วยปริมาณน้ำตรวจวัดที่จุดเข้า นว.:
        k = β·Q_obs / Q_model(now) (จำกัด ≤ 1) แยกกลุ่มจังหวัดตามแม่น้ำที่รับน้ำ
          กำแพงเพชร -> ปิง (P.17) ; พิษณุโลก พิจิตร เพชรบูรณ์ -> น่าน-ยม (N.67)
        β = สัดส่วนสูงสุดของน้ำที่สถานีซึ่งมาจากน้ำท่าในพื้นที่ 4 จังหวัด (ที่เหลือคือน้ำจากเขื่อน/จังหวัดเหนือขึ้นไป)"""
        z = self.z; pc = z["pcode"]
        scale = np.ones(len(pc)); k = {}
        for key, (codes, gcode) in CALIB_GROUPS.items():
            sel = np.isin(pc, codes)
            qobs = (gauges.get(gcode) or {}).get("q")
            qm = float(Qz[i_now, sel].sum())
            if sel.any() and qobs and qm > 0:
                k[key] = float(np.clip(beta * qobs / qm, kmin, 1.0))
                scale[sel] = k[key]
        kp = {c: float(scale[pc == c].mean()) if (pc == c).any() else 1.0 for c in UP_NAMES}
        return scale, k, kp

    def river_of_entry(self, hexid):
        r = self.ent_river.get(int(hexid), "")
        for key, (_, _, _, rivers) in REACHES.items():
            if r and r in rivers:
                return key
        return None


def hypsometry(prm, idx, hgrid):
    """F[H,n] สัดส่วนพื้นที่ที่ HAND <= h และ V[H,n] ปริมาตร (m3) ต่อ hex (1 กม² = 1e6 m2)"""
    qs = [5, 10, 25, 50, 75]
    P = np.array([np.asarray(prm[f"handM_p{q}"], float)[idx] for q in qs])     # [5,n]
    P = np.maximum.accumulate(np.maximum(P, 0), axis=0)
    top = P[-1] + 2 * (P[-1] - P[-2]) + 1.0
    xs = np.vstack([np.zeros_like(P[0]), P, top])                                # [7,n]
    fs = np.array([0.0, .05, .10, .25, .50, .75, 1.0])
    F = np.zeros((hgrid.size, idx.size))
    for j in range(idx.size):
        x = xs[:, j] + np.arange(7) * 1e-6
        F[:, j] = np.interp(hgrid, x, fs)
    dh = np.diff(hgrid, prepend=0)
    V = np.cumsum(F * dh[:, None], axis=0) * 1e6 * prm.get("hex_km2", 1.0)
    return F, V


def floodplain(prm, reach_hex, Q, qbf, S0, bank_hand=2.0, hgrid=np.arange(0, 15.01, 0.05)):
    """Q [T] m3/s ของ reach, qbf ความจุ -> depth [T, n] ซม., area_frac [T,n], S [T] m3, h [T]
    bank_hand: ความสูงตลิ่งเหนือระดับอ้างอิง HAND (ม.) — ท่วมเฉพาะเมื่อ h > ตลิ่ง, ความลึกไม่เกิน (h - ตลิ่ง) + 0.5 ม."""
    idx = np.asarray(reach_hex)
    F, V = hypsometry(prm, idx, hgrid)
    Vtot = V.sum(1)
    T = Q.size; S = np.zeros(T); s = S0
    for t in range(T):
        ex = Q[t] - qbf
        if ex > 0:
            s += ex * 3600
        else:
            s -= min(s, 0.5 * (-ex) * 3600)
        # ระเหย/ซึม 5 มม./วัน บนพื้นที่น้ำท่วม
        h = np.interp(s, Vtot, hgrid)
        area = np.interp(h, hgrid, F.sum(1)) * 1e6
        s = max(s - area * 0.005 / 24, 0)
        S[t] = s
    h = np.interp(S, Vtot, hgrid)                                                 # [T]
    hi = np.clip(np.searchsorted(hgrid, h), 0, hgrid.size - 1)
    Fh = F[hi]; Vh = V[hi]                                                        # [T,n]
    depth = np.where(Fh > 1e-3, Vh / (np.maximum(Fh, 1e-3) * 1e6 * prm.get("hex_km2", 1.0)), 0) * 100
    E = (h - bank_hand)[:, None]
    depth = np.where(E > 0, np.minimum(depth, (E + 0.5) * 100), 0)
    Fh = np.where(E > 0, Fh, 0)
    depth = depth * np.minimum(1.0, Fh / 0.25)          # hex ที่ท่วมเพียงบางส่วน ลดค่าที่แสดงตามสัดส่วนพื้นที่ท่วม
    return depth, Fh, S, h


def level_volume(prm, reach_hex, h, hgrid=np.arange(0, 15.01, 0.05)):
    F, V = hypsometry(prm, np.asarray(reach_hex), hgrid)
    return float(np.interp(h, hgrid, V.sum(1)))


def assign_reaches(prm, stations_by_code, max_km=15.0, max_hand=10.0):
    """hex -> reach จากสถานีในช่วงลำน้ำที่ใกล้ที่สุด"""
    lon = np.asarray(prm["lon"]); lat = np.asarray(prm["lat"])
    handM = np.asarray(prm.get("handM_p10", [-1] * len(lon)), float)
    pts = []
    for key, (_, _, codes, _) in REACHES.items():
        for c in codes:
            s = stations_by_code.get(c)
            if s:
                pts.append((key, s["lat"], s["lon"]))
    if not pts:
        return {}
    keys = np.array([p[0] for p in pts]); plat = np.array([p[1] for p in pts]); plon = np.array([p[2] for p in pts])
    d = np.hypot((lon[:, None] - plon[None]) * KX, (lat[:, None] - plat[None]) * KY)
    j = d.argmin(1); dj = d[np.arange(len(lon)), j]
    ok = (dj <= max_km) & (handM >= 0) & (handM < max_hand) & ~(np.asarray(prm.get("f_water", [0] * len(lon))) > 0.5)
    return {k: np.where(ok & (keys[j] == k))[0] for k in REACHES}
