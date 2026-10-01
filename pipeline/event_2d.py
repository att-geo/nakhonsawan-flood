# -*- coding: utf-8 -*-
"""
จำลองเหตุการณ์น้ำท่วมจริงย้อนหลัง (ปี 2564/2565/2568) บนโดเมน thatako_ev แล้วนำไปเทียบกับ Sentinel-1

  python pipeline/event_2d.py --site . --year 2021 [--bank-off -1.0] [--loss 0.2] [--no-north] [--tag _b1]

ข้อมูลนำเข้า hecras/thatako_ev/forcing/<year>.json (ดึงผ่าน Python ของ ArcGIS Pro เพราะเครื่องอื่นต่อ ThaiWater/Open-Meteo archive ไม่ได้):
  stations: ระดับน้ำรายชั่วโมง ThaiWater waterlevel_graph (สถานีกรมชลประทาน Y.5 โพทะเล, N.67 ชุมแสง, C.2 ปากน้ำโพ)
  rain:     Open-Meteo archive (ERA5) รายชั่วโมง lattice 0.1° 9×9 จุด
ขอบเขต:
  - cell แม่น้ำ: ผิวน้ำ = ท้องน้ำ (zbed) + ความลึกเหนือ z_ref ของสถานีที่ interpolate (IDW 2 สถานีใกล้สุด) — เหมือน run_hotspots
  - ตลิ่งใช้งาน = ตลิ่งสถานี + bank_off (ติดลบ = น้ำเข้าทุ่งได้ก่อนถึงตลิ่ง ผ่านคลอง/ประตูน้ำ/จุดต่ำที่แคบกว่า cell)
  - ปริมาณน้ำเข้าทุ่งรวม ≤ in_cap ลบ.ม./วิ (ความจุคลอง/ประตูน้ำ) — ถ้าไม่จำกัด ที่ลุ่มจะเติมจนระดับเท่าแม่น้ำ (bathtub) ท่วมเกินจริงมาก
  - ขอบเหนือ (north): cell แถวบนสุดที่ต่ำกว่าระดับน้ำ Y.5 = ขอบเขตระดับน้ำ -> น้ำบ่าจากพิจิตรเข้าทางเหนือของชุมแสง
ฝน -> น้ำท่า: SCS-CN + คันนา 100 มม. คำนวณที่จุด lattice ต่อชนิด (CN, คันนา) แล้ว IDW ไป cell (ประหยัดหน่วยความจำ ~20 เท่า)
คัน/ประตูน้ำบึงบอระเพ็ด (--hold-level): น้ำออกจากแอ่ง (พื้นที่รับน้ำ ∪ ตำบลที่ศึกษา) ไม่ได้เมื่อระดับน้ำต่ำกว่าระดับเก็บกัก
ผล: hecras/thatako_ev/runs/<year><tag>.npz (hmax, wet_h, last_wet, ความลึก ณ วันถ่ายภาพ Sentinel-1) + .json (สรุปรายตำบล)
"""
import argparse, json, math, os, sys, time
from datetime import datetime, timezone, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model2d  # noqa: E402
from run_hotspots import _runoff, _idw4, _ponding  # noqa: E402

TZ7 = timezone(timedelta(hours=7))
USE = ["Y.5", "N.67", "C.2"]                         # สถานีกรมชลประทาน (รายชั่วโมง, สะอาดกว่าโทรมาตร)
BANK = {"Y.5": 30.15, "N.67": 28.21, "C.2": 25.70}   # ระดับตลิ่ง (ม.รทก.) จาก ThaiWater
# โดเมนทั้งจังหวัด (prov_ev): สถานีต่อลำน้ำ + กลุ่มสำหรับจำกัดน้ำเข้าทุ่ง (ความจุคลอง/ประตูน้ำ) แยกลำน้ำ
USE_BY_HID = {"prov_ev": ["P.16", "P.17", "PIN005", "Y.5", "N.67", "C.2", "CPY002", "C.13", "Ct.5A", "Ct.4", "Ct.19", "Ct.2A"]}
GROUPS = ["nan", "ping", "cpy", "skg"]                # น่าน–ยม, ปิง, เจ้าพระยา, แม่วงก์–สะแกกรัง
GROUP_OF = {"Y.5": "nan", "N.67": "nan", "NAN008": "nan", "P.16": "ping", "P.17": "ping", "PIN005": "ping", "C.2": "cpy", "CPY001": "cpy",
            "CPY002": "cpy", "C.13": "cpy", "Ct.5A": "skg", "Ct.4": "skg", "Ct.19": "skg", "Ct.2A": "skg"}
NORTH_LON = (100.10, 100.62)                          # ขอบเหนือที่รับน้ำบ่าจากพิจิตร (ที่ราบยม–น่าน) ด้วยระดับน้ำ Y.5


def clean_series(rows, t0, T, lo=5, hi=60):
    """[[datetime_local, wl, q], ...] -> ระดับน้ำรายชั่วโมง T ค่า เริ่ม t0 (UTC epoch) : ตัดค่าผิดปกติ + เติมช่องว่าง"""
    t = np.array([datetime.strptime(r[0], "%Y-%m-%d %H:%M").replace(tzinfo=TZ7).timestamp() for r in rows])
    v = np.array([float(r[1]) for r in rows])
    ok = (v > lo) & (v < hi)
    t, v = t[ok], v[ok]
    # despike: ห่างจากมัธยฐานเคลื่อนที่ 25 ค่าเกิน 0.8 ม.
    med = np.array([np.median(v[max(i - 12, 0):i + 13]) for i in range(v.size)])
    ok = np.abs(v - med) < 0.8
    t, v = t[ok], v[ok]
    tg = t0 + 3600 * np.arange(T)
    return np.interp(tg, t, v, left=v[0], right=v[-1])


def s1_snap_hours(t0, T, year, s1_dir):
    """ชั่วโมงของภาพ Sentinel-1 ภายในช่วงจำลอง : วงโคจร 62 (ขาลง) ~23:09 UTC, 172 (ขาขึ้น) ~11:29 UTC
    ชื่อไฟล์ <date>_wet.npz (ท่าตะโก, วงโคจร 62) หรือ <date>_o<orbit>_wet.npz (ทั้งจังหวัด)"""
    out = {}
    if s1_dir and os.path.isdir(s1_dir):
        for f in sorted(os.listdir(s1_dir)):
            if f.startswith(str(year)) and f.endswith("_wet.npz"):
                key = f[:-len("_wet.npz")]
                hh = "11:30" if "_o172" in key else "23:00"
                ts = datetime.fromisoformat(f[:10] + f"T{hh}:00+00:00").timestamp()
                h = int(round((ts - t0) / 3600))
                if 0 < h < T:
                    out[key] = h
    return out


# ---- บึงบอระเพ็ด: DEM (FABDEM/TanDEM-X) เห็นแค่ผิวน้ำตอนถ่าย (~24–25 ม.) จึงแทบไม่มีความจุใต้ +24 ม. (แบบจำลอง 27 ล้าน ลบ.ม. vs จริง 180)
# ค่าอ้างอิง ชป. (water.rid.go.th …/O&MQA/66/a/3.5.pdf): ระดับเก็บกักปกติ +24.00 ม. = 180.3 ล้าน ลบ.ม., ต่ำสุด +20.87 ม. = 8.29 ล้าน ลบ.ม. ;
# 19 ก.ย. 2564: +21.98 ม. = 31.3 ล้าน ลบ.ม. 19,524 ไร่ (ผู้จัดการ) ; พื้นที่–ปริมาตรรายวันปี 2567 จากประมงจังหวัด (A ≈ 125–160 กม² ที่ ~180 ล้าน ลบ.ม.)
# -> พื้นที่ผิวน้ำ A(h) แบบเส้นตรงเป็นช่วง ; V(20.87) ≈ 4, V(21.98) ≈ 28, V(24.0) ≈ 185 ล้าน ลบ.ม.
BUENG_A = ((20.0, 0.0), (20.87, 10.0), (21.98, 32.0), (23.0, 75.0), (24.0, 130.0), (24.86, 204.0))  # (ระดับ ม.รทก., พื้นที่ กม²)
# จุดสุดท้าย: 23 ก.ย. 2568 +24.86 ม. ผิวน้ำ 127,344 ไร่ (ปชส.เขต 4) ≈ พื้นที่บึงเต็ม (ประมง 2567: ~204 กม² เมื่อ > 280 ล้าน ลบ.ม.)
BUENG_SEED = (15.705, 100.25)                          # จุดในแอ่งบึง (ใช้หาผืนบึงที่เชื่อมกัน)
BUENG_INIT = {2021: 21.8, 2022: 22.0, 2024: 22.4, 2025: 22.0}   # ระดับบึง 20 ส.ค. (2564 จากข่าว 14–19 ก.ย. ; 2567 จากปริมาตรประมง 18 ส.ค. ; ปีอื่นสมมติ ~ต่ำสุดตาม rule curve)


def burn_boraphet(dem, hold, LA, LO, dx, top=25.6, log=print):
    """ลดระดับ DEM ใน cell ต่ำสุดของผืนบึง ให้ความสัมพันธ์ระดับ–พื้นที่ใต้ +24 ม. ตรงกับ BUENG_A ; คืน (dem ใหม่, mask บึง)"""
    from scipy import ndimage
    cand = hold & (dem < top)
    lab, _ = ndimage.label(cand)
    i0 = np.unravel_index(np.argmin((LA - BUENG_SEED[0]) ** 2 + (LO - BUENG_SEED[1]) ** 2), dem.shape)
    k = lab[i0] if lab[i0] else np.bincount(lab[cand]).argmax()
    lake = lab == k
    ca = dx * dx / 1e6
    idx = np.flatnonzero(lake.ravel()); order = idx[np.argsort(dem.ravel()[idx])]
    a = (np.arange(order.size) + 0.5) * ca                                   # พื้นที่สะสมจากต่ำไปสูง
    hs = np.array([x[0] for x in BUENG_A]); As = np.array([x[1] for x in BUENG_A])
    znew = np.where(a <= As[-1], np.interp(a, As, hs), np.maximum(dem.ravel()[order], hs[-1]))   # เกินพื้นที่บึง: ไม่ต่ำกว่าระดับสุดท้าย
    d2 = dem.copy().ravel(); v0 = float(np.maximum(24.0 - d2[order], 0).sum() * ca)
    d2[order] = np.minimum(d2[order], znew)
    d2 = d2.reshape(dem.shape)
    v1 = float(np.maximum(24.0 - d2[lake], 0).sum() * ca)
    log(f"  บึงบอระเพ็ด: ผืนบึง {lake.sum() * ca:.0f} กม² ; ความจุใต้ +24 ม. {v0:.0f} -> {v1:.0f} ล้าน ลบ.ม.")
    return d2.astype(np.float32), lake


def poly_mask(features, LA, LO):
    """even-odd point-in-polygon ของจุดกลาง cell"""
    out = np.zeros(LA.shape, bool)
    for f in features:
        g = f["geometry"]; polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            for ring in poly:
                r = np.asarray(ring); c = np.zeros(LA.shape, bool)
                xa, ya, xb, yb = r[:-1, 0], r[:-1, 1], r[1:, 0], r[1:, 1]
                for i in range(len(xa)):
                    cond = (ya[i] > LA) != (yb[i] > LA)
                    xi = xa[i] + (LA - ya[i]) * (xb[i] - xa[i]) / (yb[i] - ya[i] if yb[i] != ya[i] else 1e-12)
                    c ^= cond & (LO < xi)
                out ^= c
    return out


class LazyRain:
    """น้ำท่ารายชั่วโมงราย cell = Σ w·Q[t, จุด lattice, ชนิดพื้นที่] (ไม่เก็บ [T, ny, nx] ทั้งก้อน)"""
    def __init__(self, Q, nn, w, inv, shape):
        self.Q, self.nn, self.w, self.inv, self.shape = Q, nn, w, inv, shape

    def __len__(self):
        return self.Q.shape[0]

    def __getitem__(self, t):
        q = self.Q[t]                                        # [pts, U]
        return (q[self.nn, self.inv[:, None]] * self.w).sum(1).reshape(self.shape).astype(np.float32)


def run(site, year, start="08-20", end="12-15", bank_off=0.0, loss=0.2, north=True, tag="", hid="thatako_ev",
        s1_dir=None, n_mult=1.0, hold_level=None, in_cap=None, bueng=False, bueng_init=None, forcing=None, out_dir=None, log=print):
    """forcing: dict รูปแบบเดียวกับ forcing/<ปี>.json (ใช้แทนไฟล์ เช่น สถานการณ์สมมติ) ; out_dir: โฟลเดอร์ผลแทน hecras/<hid>/runs"""
    hs = os.path.join(site, "data", "static", "hotspots")
    d = np.load(os.path.join(hs, f"{hid}.npz")); dom = {k: d[k] for k in d.files if k != "meta"}; m = json.loads(str(d["meta"]))
    F = forcing if forcing is not None else json.load(open(os.path.join(site, "hecras", hid, "forcing", f"{year}.json")))
    ny, nx = dom["dem"].shape
    t0 = datetime.fromisoformat(f"{year}-{start}T00:00:00+00:00").timestamp()
    t1 = datetime.fromisoformat(f"{year}-{end}T00:00:00+00:00").timestamp()
    T = int((t1 - t0) // 3600) + 1
    # ---- ฝน -> น้ำท่า (อุ่นเครื่องตั้งแต่ต้นข้อมูล 1 ส.ค.)
    R = F["rain"]; rt0 = datetime.fromisoformat(R["t0_utc"] + ":00+00:00").timestamp()
    P = np.nan_to_num(np.array(R["P"], np.float32)).T               # [Tr, pts]
    i0 = int((t0 - rt0) // 3600)
    Pw = P[:i0 + T]
    cn = np.where(dom["cn"] > 0, dom["cn"], 80).ravel().astype(np.float32)
    bk = np.where(dom["crop"].ravel(), 100.0, 10.0).astype(np.float32)
    key = cn * 1000 + bk; uk, inv = np.unique(key, return_inverse=True)
    U = uk.size; npt = Pw.shape[1]
    Pr = np.repeat(Pw, U, axis=1)                                   # [T, pts*U]
    Q = _runoff(Pr, np.tile((uk // 1000).astype(np.float32), npt), np.tile((uk % 1000).astype(np.float32), npt), loss=0.25)
    Q = Q.reshape(Q.shape[0], npt, U)[i0:]
    (sw, ne) = m["bounds"]; la = np.linspace(ne[0], sw[0], ny); lo = np.linspace(sw[1], ne[1], nx)
    LA, LO = np.meshgrid(la, lo, indexing="ij")
    nn, w = _idw4(LA, LO, np.array(R["lat"]), np.array(R["lon"]))
    rain_eff = LazyRain(Q, nn, w, inv, (ny, nx))
    # ---- ระดับน้ำสถานี
    st = {s["code"]: s for s in m["stations"]}
    sm = F.get("station_meta", {})
    for c, e in sm.items():                  # สถานีที่ไม่อยู่ในโดเมนตอนสร้าง: z_ref = ค่าต่ำสุดของ DEM รอบสถานี (±1 cell)
        if c not in st and e.get("lat") is not None:
            X_ = math.radians(e["lon"]) * 6378137.0; Y_ = math.log(math.tan(math.pi / 4 + math.radians(e["lat"]) / 2)) * 6378137.0
            ex = m["extent_webm"]; cw = m["cell_webm"]
            ci_, ri_ = int((X_ - ex[0]) // cw), int((ex[3] - Y_) // cw)
            if 0 <= ri_ < ny and 0 <= ci_ < nx:
                win = dom["dem"][max(ri_ - 1, 0):ri_ + 2, max(ci_ - 1, 0):ci_ + 2]
                st[c] = {"code": c, "name": e["name"], "lat": e["lat"], "lon": e["lon"], "z_ref": round(float(win[win > 0].min()), 2)}
    bank_of = dict(BANK); bank_of.update({c: float(e["min_bank"]) for c, e in sm.items() if e.get("min_bank")})
    use = [c for c in USE_BY_HID.get(hid, USE) if c in st and c in bank_of and isinstance(F["stations"].get(c), list) and len(F["stations"][c]) > 100]
    WL = {}
    for c in list(use):
        try:
            WL[c] = clean_series(F["stations"][c], t0, T, lo=st[c]["z_ref"] - 3, hi=bank_of[c] + 8)
        except (ValueError, IndexError):
            use.remove(c)
    cgrp = None
    if hid in USE_BY_HID:
        # หลายลำน้ำในโดเมนเดียว: cell แม่น้ำใช้เฉพาะสถานีในลำน้ำ (กลุ่ม) เดียวกัน — ไม่เฉลี่ยข้ามลำน้ำ
        # (เช่น แม่วงก์ไม่ควรได้ความลึกจาก C.2) ; ลำน้ำที่ไม่มีข้อมูลสถานีเลย -> ถือเป็นพื้นดินธรรมดา (ไหลตาม DEM)
        codes_b = [s_["code"] for s_ in m["stations"]]
        rv = dom["river"]; g_cell = np.full(rv.shape, -1, np.int16)
        for k_, c_ in enumerate(codes_b):
            g_cell[rv == k_] = GROUPS.index(GROUP_OF.get(c_, "cpy"))
        have = {GROUPS.index(GROUP_OF.get(c, "cpy")) for c in use}
        rv2 = np.where(np.isin(g_cell, list(have)), rv, -1).astype(rv.dtype)
        dom = dict(dom); dom["river"] = rv2
        log(f"  cell แม่น้ำ {int((rv >= 0).sum())} -> ใช้ {int((rv2 >= 0).sum())} (ลำน้ำที่มีสถานี: {sorted(GROUPS[g] for g in have)})")
        cgrp = g_cell
    rr, cc = np.where(dom["river"] >= 0)
    zb = (dom["zbed"] if "zbed" in dom else dom["dem"])[rr, cc].astype(np.float32)
    slat = np.array([st[c]["lat"] for c in use]); slon = np.array([st[c]["lon"] for c in use])
    dk = np.hypot((lo[cc][:, None] - slon[None]) * 107.1, (la[rr][:, None] - slat[None]) * 110.6)
    if cgrp is not None:
        sg = np.array([GROUPS.index(GROUP_OF.get(c, "cpy")) for c in use])
        dk = dk + 1e4 * (cgrp[rr, cc][:, None] != sg[None])
    nn2 = np.argsort(dk, 1)[:, :2]
    wk = 1 / np.maximum(np.take_along_axis(dk, nn2, 1), 0.3) ** 2; wk /= wk.sum(1, keepdims=True)
    D = np.array([np.maximum(WL[c] - st[c]["z_ref"], 0) for c in use])            # [K, T]
    BH = np.array([max(bank_of[c] - st[c]["z_ref"], 0.5) for c in use])
    stage_r = lambda hr: zb + (D[nn2, min(hr, T - 1)] * wk).sum(1)
    if isinstance(bank_off, dict):                  # ตลิ่งใช้งานรายกลุ่มลำน้ำ {"nan": -1.5, "cpy": -2.0, ...}
        gcell = cgrp[rr, cc] if cgrp is not None else np.zeros(rr.size, int)
        boff = np.array([float(bank_off.get(GROUPS[g], -1.0)) for g in gcell], np.float32)
    else:
        boff = bank_off
    bank_r = zb + (BH[nn2] * wk).sum(1) + boff
    # ---- ขอบเหนือ: ระดับน้ำ Y.5 (สัมบูรณ์) กับ cell แถวบน 2 แถว
    river = dom["river"].copy()
    n_edge = 0
    if north and "Y.5" in WL:
        edge = np.zeros_like(river, bool); edge[:2, :] = True
        edge &= (LO >= NORTH_LON[0]) & (LO <= NORTH_LON[1])
        edge &= dom["valid"].astype(bool) & (river < 0)
        river[edge] = 0
        n_edge = int(edge.sum())
    dom2 = dict(dom); dom2["river"] = river
    if n_mult != 1.0:                                                               # ความขรุขระที่ราบ (คันนา/พืช) -> น้ำไหลช้าลง
        dom2["n"] = np.where(dom["river"] >= 0, dom["n"], dom["n"] * n_mult).astype(np.float32)
    rr2, cc2 = np.where(river >= 0)
    is_r = dom["river"][rr2, cc2] >= 0                                              # ลำดับของ np.where(river>=0) ใหม่
    pos_r = np.full(river.shape, -1, np.int32); pos_r[rr, cc] = np.arange(rr.size)
    idx_r = pos_r[rr2, cc2]
    z_edge = dom["dem"][rr2, cc2].astype(np.float32)
    wl_y5 = WL.get("Y.5")

    def stage_fn(hr):
        s = np.empty(rr2.size, np.float32)
        s[is_r] = stage_r(hr)[idx_r[is_r]]
        if (~is_r).any():
            s[~is_r] = np.maximum(wl_y5[min(hr, T - 1)], z_edge[~is_r])             # ต่ำกว่าพื้น = แห้ง (ไม่ไหลเข้า)
        return s
    bank = np.empty(rr2.size, np.float32); bank[is_r] = bank_r[idx_r[is_r]]; bank[~is_r] = z_edge[~is_r]
    snaps = s1_snap_hours(t0, T, year, s1_dir)
    hold = None
    if hold_level is not None:            # แอ่งบึงบอระเพ็ด = พื้นที่รับน้ำที่ไหลลงพื้นที่ศึกษา ∪ ตำบลที่ศึกษา (ไม่รวมแม่น้ำ)
        fs = []
        for fn_ in ("thatako_contrib.geojson", "thatako_aoi.geojson"):   # คงกรอบเดิมแม้โดเมนใหญ่ขึ้น
            fs.append(poly_mask(json.load(open(os.path.join(hs, fn_), encoding="utf8"))["features"], LA, LO))
        hold = (fs[0] | fs[1]) & (dom["river"] < 0)
    h0 = None
    if bueng and hold is not None:
        dem_b, lake = burn_boraphet(dom2["dem"].astype(np.float32), hold, LA, LO, float(m["dx"]), log=log)
        dom2 = dict(dom2); dom2["dem"] = dem_b
        if "zbed" in dom2:
            dom2["zbed"] = np.minimum(dom2["zbed"], dem_b)
        lv = bueng_init if bueng_init is not None else BUENG_INIT.get(year)
        if lv is not None:
            h0 = np.where(lake, np.maximum(lv - dem_b, 0), 0).astype(np.float32)
            log(f"  ระดับบึงเริ่มต้น +{lv:.2f} ม. = {float(h0.sum() * float(m['dx']) ** 2 / 1e6):.0f} ล้าน ลบ.ม.")
    # ความจุน้ำเข้าทุ่ง: ค่าเดียว (ทั้งโดเมน) หรือ dict รายกลุ่มลำน้ำ {"nan": 400, "ping": 300, ...}
    grp = None; ofn = None
    if isinstance(in_cap, dict):
        gi = np.array([GROUPS.index(GROUP_OF.get(c, "cpy")) for c in use])
        rg_r = cgrp[rr, cc] if cgrp is not None else gi[nn2[:, 0]]                  # กลุ่มลำน้ำของ cell
        grp = np.full(river.shape, -1, np.int32); grp[rr, cc] = rg_r
        grp[(river >= 0) & (dom["river"] < 0)] = GROUPS.index("nan")                # ขอบเหนือ = น่าน–ยม
        caps = np.array([float(in_cap.get(g, 1e9)) for g in GROUPS], np.float32)
        ofn = lambda hr: caps
    elif in_cap:
        ofn = lambda hr: float(in_cap)
    tt = time.time()
    r = model2d.run2d(dom2, m, rain_eff, T - 1, stage_fn, h0=h0, loss_mmh=loss, snap_hours=tuple(snaps.values()),
                      river_bank=bank, overflow_fn=ofn, dur_from=0, zones=dom["aoi"], n_zones=len(m["aoi"]),
                      hold_mask=hold, hold_level=hold_level, river_grp=grp)
    rt = round(time.time() - tt, 1)
    od = out_dir or os.path.join(site, "hecras", hid, "runs"); os.makedirs(od, exist_ok=True)
    name = f"{year}{tag}"
    np.savez_compressed(os.path.join(od, name + ".npz"), hmax=r["hmax"].astype(np.float16), wet_h=r["wet_h"].astype(np.float16),
                        last_wet=r["last_wet"].astype(np.float32), h_end=r["h"].astype(np.float16), zseries=r["zseries"],
                        snap_dates=np.array(list(snaps.keys())),
                        snaps=np.stack([r["snaps"].get(h, np.zeros((ny, nx), np.float32)) for h in snaps.values()]).astype(np.float16)
                        if snaps else np.zeros((0, ny, nx), np.float16))
    r["snaps"][0] = r["hmax"]
    domp = dom
    if bueng and hold is not None:                 # ผืนบึงบอระเพ็ด = แหล่งน้ำถาวร (ไม่นับเป็นน้ำท่วมขัง) — _ponding ตัด cell ที่ n = 0.03
        domp = dict(dom); domp["n"] = np.where(lake, np.float32(0.03), dom["n"]).astype(np.float32)
    pd, _ = _ponding(m, domp, r, 1, 0, T, None, m["dx"])
    summ = {"year": year, "start": f"{year}-{start}", "end": f"{year}-{end}", "bank_off": bank_off, "loss_mmh": loss, "north": north, "n_mult": n_mult, "hold_level": hold_level, "in_cap_m3s": in_cap, "bueng_bathy": bool(bueng), "bueng_init": (bueng_init if bueng_init is not None else BUENG_INIT.get(year)) if bueng else None,
            "n_edge_cells": n_edge, "stations": use, "runtime_s": rt, "ponding": pd,
            "wl_max": {c: round(float(WL[c].max()), 2) for c in use}, "snap_dates": list(snaps.keys())}
    json.dump(summ, open(os.path.join(od, name + ".json"), "w", encoding="utf8"), ensure_ascii=False)
    log(name, pd["total_new"], rt)
    return summ


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="."); ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--start", default="08-20"); ap.add_argument("--end", default="12-15")
    ap.add_argument("--bank-off", default="0", help="ม. (ค่าเดียว) หรือรายกลุ่มลำน้ำ nan=-1.5,ping=-1.5,cpy=-2,skg=-1"); ap.add_argument("--loss", type=float, default=0.2)
    ap.add_argument("--no-north", action="store_true"); ap.add_argument("--tag", default="")
    ap.add_argument("--s1-dir", default=None); ap.add_argument("--n-mult", type=float, default=1.0)
    ap.add_argument("--hold-level", type=float, default=None, help="ระดับเก็บกักแอ่งบึงบอระเพ็ด (ม.รทก.) ; ไม่ใส่ = ไม่มีคัน/ประตูน้ำ")
    ap.add_argument("--in-cap", default=None, help="ปริมาณน้ำเข้าทุ่งจากแม่น้ำรวมสูงสุด (ลบ.ม./วิ) — ความจุคลอง/ประตูน้ำ ; ไม่ใส่ = ไม่จำกัด ; "
                    "รายกลุ่มลำน้ำ: nan=400,ping=300,cpy=200,skg=100")
    ap.add_argument("--hid", default="thatako_ev")
    ap.add_argument("--bueng", action="store_true", help="ปรับความจุบึงบอระเพ็ดใต้ +24 ม. ตามข้อมูล ชป. + ระดับบึงเริ่มต้นรายปี (BUENG_INIT)")
    ap.add_argument("--bueng-init", type=float, default=None)
    a = ap.parse_args()
    cap = a.in_cap
    if cap is not None:
        cap = {k: float(v) for k, v in (x.split("=") for x in cap.split(","))} if "=" in cap else float(cap)
    bo = {k: float(v) for k, v in (x.split("=") for x in a.bank_off.split(","))} if "=" in a.bank_off else float(a.bank_off)
    run(a.site, a.year, a.start, a.end, bo, a.loss, not a.no_north, a.tag, hid=a.hid, s1_dir=a.s1_dir, n_mult=a.n_mult,
        hold_level=a.hold_level, in_cap=cap, bueng=a.bueng, bueng_init=a.bueng_init)
