# -*- coding: utf-8 -*-
"""
ประวัติระดับน้ำ/ปริมาณน้ำรายชั่วโมงของสถานีกรมชลประทาน (ThaiWater waterlevel_graph) และการวิเคราะห์
  - rating curve  Q = a·(WL − z0)^b  ฟิตจากคู่ (ระดับน้ำ, ปริมาณน้ำ) ในอดีต — ใช้เติม Q เมื่อสถานีไม่รายงานปริมาณน้ำ
  - ค่าคงที่การลดลงของน้ำ (recession constant k, ชม.) จากช่วงน้ำลดในอดีต:  Q(t) = Qb + (Q0 − Qb)·e^(−t/k)
  - แนวโน้มปัจจุบัน (น้ำกำลังขึ้น/ลง) และจำนวนชั่วโมงที่ระดับน้ำอยู่เหนือตลิ่งต่อเนื่อง
ใช้แทนสมมติฐานเดิม "ปริมาณน้ำคงที่เท่าค่าตรวจวัดตลอด 72 ชม." ในการพยากรณ์น้ำล้นตลิ่ง
"""
import json, math, os, time
from datetime import datetime
import numpy as np

KEEP_DAYS = 30
K_DEFAULT = {"ping": 120.0, "nan": 150.0, "cpy": 150.0, "maewong": 48.0}   # ชม. ใช้เมื่อประวัติไม่พอ
K_RANGE = (12.0, 360.0)
TAU_RISE = 12.0                                                            # ชม. ช่วงที่น้ำยังขึ้นต่อ (damped trend)


def _ts(s, tz):
    return int(datetime.strptime(s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=tz).timestamp())


def update(path, stations, get_json, tw, tz, now_ts=None, keep_days=KEEP_DAYS):
    """เก็บประวัติ {code: [[t, q, wl], ...]} ย้อนหลัง keep_days วัน
    ดึงย้อนหลังจาก waterlevel_graph เฉพาะเมื่อยังไม่มีประวัติหรือข้อมูลขาดช่วง > 90 นาที (ปกติใช้ค่าล่าสุดของแต่ละรอบ)"""
    now_ts = now_ts or time.time()
    hist = {}
    if os.path.exists(path):
        try:
            hist = json.load(open(path, encoding="utf8"))
        except Exception:  # noqa
            hist = {}
    meta = hist.pop("_meta", {}) if isinstance(hist.get("_meta"), dict) else {}
    full = meta.get("full", {})
    cut = now_ts - keep_days * 86400
    n_fetch = 0
    for s in stations:
        code = s["code"]
        h = {int(r[0]): r for r in hist.get(code, []) if r[0] >= cut}
        t_obs = _ts(s["time"], tz) if s.get("time") else None
        last = max(h) if h else None
        need_full = s.get("sid") and (now_ts - full.get(code, 0) > 86400) and (not h or min(h) > cut + 2 * 86400)
        gap = s.get("sid") and last is not None and t_obs is not None and t_obs - last > 5400
        if need_full or gap:
            t_from = cut if need_full else last - 3600
            d0 = datetime.fromtimestamp(t_from, tz).strftime("%Y-%m-%d")
            d1 = datetime.fromtimestamp(now_ts, tz).strftime("%Y-%m-%d")
            try:
                g = get_json(tw + f"waterlevel_graph?station_type=tele_waterlevel&station_id={s['sid']}"
                             f"&start_date={d0}&end_date={d1}", tries=2, timeout=45)["data"]["graph_data"]
                for x in g:
                    if x.get("value") is None:
                        continue
                    t = _ts(x["datetime"], tz)
                    if t >= cut:
                        q = x.get("discharge")
                        h[t] = [t, None if q is None else float(q), round(float(x["value"]), 3)]
                n_fetch += 1
                if need_full:
                    full[code] = int(now_ts)
            except Exception:  # noqa
                pass
        if t_obs is not None:
            h[t_obs] = [t_obs, s.get("q"), s["wl"]]
        hist[code] = [h[k] for k in sorted(h)]
    out = dict(hist); out["_meta"] = {"full": full}
    json.dump(out, open(path, "w", encoding="utf8"), separators=(",", ":"))
    return hist, n_fetch


# ---------------------------------------------------------------- rating curve
def fit_rating(rows):
    a = np.array([[r[2], r[1]] for r in rows if r[1] is not None and r[1] > 0 and r[2] is not None], float)
    if len(a) < 24 or np.ptp(a[:, 0]) < 0.3:
        return None
    wl, q = a.T
    y = np.log(q); best = None
    for z0 in np.linspace(wl.min() - 4.0, wl.min() - 0.05, 40):
        x = np.log(wl - z0)
        b, la = np.polyfit(x, y, 1)
        if not (1.0 <= b <= 3.5):
            continue
        e = float(((y - (la + b * x)) ** 2).mean())
        if best is None or e < best[0]:
            best = (e, z0, b, la)
    if best is None:
        return None
    e, z0, b, la = best
    return {"a": math.exp(la), "b": float(b), "z0": float(z0), "rmse_log": round(math.sqrt(e), 3),
            "n": int(len(a)), "wl_max": float(wl.max()), "q_max": float(q.max())}


def q_rating(rt, wl):
    """เหนือระดับสูงสุดที่เคยวัด Q ใช้เลขชี้กำลังไม่เกิน 5/3 (น้ำส่วนเกินแผ่ออกที่ราบ ไม่เพิ่มในลำน้ำเร็วเท่าเดิม)"""
    wl = np.asarray(wl, float); z0, a, b = rt["z0"], rt["a"], rt["b"]
    q = a * np.maximum(wl - z0, 0) ** b
    wm = rt.get("wl_max")
    if wm is not None:
        qm = a * max(wm - z0, 1e-6) ** b
        q = np.where(wl > wm, qm * (np.maximum(wl - z0, 0) / max(wm - z0, 1e-6)) ** min(b, 5 / 3), q)
    return q


def q_manning(st, wl):
    """ประมาณ Q จากความจุลำน้ำ Q ≈ qmax·((WL − ท้องน้ำ)/(ตลิ่ง − ท้องน้ำ))^(5/3)"""
    g, b, qm = st.get("ground"), st.get("bank"), st.get("qmax")
    if g is None or b is None or not qm or b - g <= 0.2:
        return None
    return qm * np.clip((np.asarray(wl, float) - g) / (b - g), 0, None) ** (5 / 3)


def series(rows, st, t_grid):
    """Q รายชั่วโมงบนแกนเวลา t_grid (วินาที): ค่าตรวจวัด > rating curve > Manning; เติมช่องว่าง ≤ 6 ชม."""
    if not rows:
        return None, None, "none"
    rt = fit_rating(rows)
    t = np.array([r[0] for r in rows], float); wl = np.array([r[2] for r in rows], float)
    qo = np.array([np.nan if r[1] is None else r[1] for r in rows], float)
    if rt is not None:
        qa = q_rating(rt, wl); src = "rating"
    else:
        qa = q_manning(st, wl); src = "manning"
        if qa is None:
            qa = np.full_like(wl, np.nan); src = "obs"
    q = np.where(np.isnan(qo), qa, qo)
    ok = ~np.isnan(q)
    if ok.sum() < 2:
        return None, rt, src
    t, q, wl = t[ok], q[ok], wl[ok]
    qg = np.interp(t_grid, t, q, left=np.nan, right=np.nan)
    wg = np.interp(t_grid, t, wl, left=np.nan, right=np.nan)
    near = np.abs(t[np.clip(np.searchsorted(t, t_grid), 0, len(t) - 1)] - t_grid)
    near = np.minimum(near, np.abs(t[np.clip(np.searchsorted(t, t_grid) - 1, 0, len(t) - 1)] - t_grid))
    far = near > 6 * 3600
    qg[far] = np.nan; wg[far] = np.nan
    return {"q": qg, "wl": wg}, rt, src


# ---------------------------------------------------------------- recession / trend
def recession(q, k_default):
    """k (ชม.) จากชั่วโมงที่น้ำลดต่อเนื่อง ≥ 6 ชม. และสูงกว่าน้ำฐาน: k = −1 / median(Δ ln(Q − Qb))"""
    q = np.asarray(q, float)
    v = q[~np.isnan(q)]
    if v.size < 48:
        return k_default, None, 0
    qb = float(np.percentile(v, 5)) * 0.9
    qs = np.convolve(np.nan_to_num(q, nan=-1), np.ones(3) / 3, "same")
    qs[np.convolve(np.isnan(q).astype(float), np.ones(3), "same") > 0] = np.nan
    ex = qs - qb
    d = np.full_like(qs, np.nan)
    good = (ex[1:] > 0.5 * qb + 1) & (ex[:-1] > 0.5 * qb + 1) & ~np.isnan(ex[1:]) & ~np.isnan(ex[:-1])
    d[1:][good] = np.log(ex[1:][good]) - np.log(ex[:-1][good])
    fall = np.nan_to_num(d, nan=0) < 0
    sel = np.zeros_like(fall)
    i = 0
    while i < len(fall):                                   # ช่วงน้ำลดต่อเนื่อง ≥ 6 ชม.
        if fall[i]:
            j = i
            while j < len(fall) and fall[j]:
                j += 1
            if j - i >= 6:
                sel[i:j] = True
            i = j
        else:
            i += 1
    if sel.sum() < 12:
        return k_default, qb, int(sel.sum())
    k = -1.0 / float(np.median(d[sel]))
    return float(np.clip(k, *K_RANGE)), qb, int(sel.sum())


def trend(q, hours=6):
    """อัตราการเปลี่ยนแปลง (ลบ.ม./วิ ต่อ ชม.) ช่วง hours ชม.ล่าสุด"""
    v = np.asarray(q, float)
    idx = np.where(~np.isnan(v))[0]
    if idx.size < 2:
        return 0.0
    i1 = idx[-1]; i0 = idx[idx >= i1 - hours][0]
    return 0.0 if i1 == i0 else float((v[i1] - v[i0]) / (i1 - i0))


def forecast(q0, rate, k, qb, n, tau=TAU_RISE):
    """Q ในอนาคต n ชม. (ชม.ที่ 0 = ตอนนี้): ถ้าน้ำกำลังขึ้น ขึ้นต่อแบบหน่วง (τ) จนถึงยอด แล้วลดแบบ exponential ลงหา Qb"""
    t = np.arange(n, dtype=float)
    qb = min(qb if qb is not None else 0.0, q0)
    if rate > 0:
        rate = min(rate, 0.05 * q0)
        tp = tau; qpk = q0 + rate * tau * (1 - math.exp(-1))
        up = q0 + rate * tau * (1 - np.exp(-t / tau))
        dn = qb + (qpk - qb) * np.exp(-(t - tp) / k)
        return np.where(t <= tp, up, dn)
    return qb + (q0 - qb) * np.exp(-t / k)


def overbank_since(rows, bank):
    """เวลา (วินาที) ที่ระดับน้ำเริ่มอยู่เหนือตลิ่งในช่วงล่าสุด, และระดับน้ำตอนนี้ยังเหนือตลิ่งหรือไม่"""
    if not rows or bank is None:
        return None, False
    over_now = rows[-1][2] > bank
    start = None
    for r in reversed(rows):
        if r[2] > bank:
            start = r[0]
        elif start is not None:
            break
    return start, over_now
