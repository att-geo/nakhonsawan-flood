# -*- coding: utf-8 -*-
"""
รันแบบจำลอง 2D rain-on-grid ของจุดวิกฤต (ต้องรัน pipeline/run_update.py ก่อน เพื่อให้มี rain_cache / hex_state / upstream)

  python pipeline/run_hotspots.py --site . [--only mueang] [--hecras]

ผลลัพธ์ data/live/hotspots/<id>_{now,p24,p48,p72,max72}.png + hotspots_live.json
"""
import argparse, json, math, os, sys, time
from datetime import datetime, timezone, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model2d, upstream as upm  # noqa: E402

TZ = timezone(timedelta(hours=7))
PAST_H, FC_H = 48, 72


def _stage_series(meta, stations, rivers, hist, times, i0, i_now, T):
    """ความลึกน้ำในลำน้ำ (wl - z_ref) รายชั่วโมงของแต่ละสถานีในโดเมน: อดีตจากประวัติ, อนาคตจาก Q พยากรณ์ผ่าน rating h ~ Q^0.6"""
    by = {s["code"]: s for s in stations}
    rq = {}
    for r in rivers or []:
        if r.get("q_obs"):
            tt = np.array(r["t"]); qf = np.array([np.nan if v is None else v for v in r["q_fc"]], float)
            rq[r["gauge"]] = (tt, qf, r["q_obs"])
    reach_of = {c: key for key, (_, g, codes, _) in upm.REACHES.items() for c in codes}
    gauge_of = {key: g for key, (_, g, _, _) in upm.REACHES.items()}
    out = {}
    for k, s in enumerate(meta["stations"]):
        cur = by.get(s["code"])
        if not cur:
            continue
        zref = s["z_ref"]; ground = cur.get("ground") or (cur["wl"] - 3)
        ser = np.full(T, cur["wl"], float)
        h = hist.get(s["code"], [])
        if h:
            ht = np.array([x[0] for x in h]); hw = np.array([x[2] for x in h], float)
            past = times[i0:i_now + 1]
            ser[:i_now - i0 + 1] = np.interp(past, ht, hw, left=hw[0], right=cur["wl"])
        g = gauge_of.get(reach_of.get(s["code"], ""), "")
        if g in rq:
            tt, qf, q0 = rq[g]
            fut = times[i_now:i_now + T - (i_now - i0)]
            ratio = np.interp(fut, tt[~np.isnan(qf)], qf[~np.isnan(qf)] / q0) if (~np.isnan(qf)).any() else np.ones(fut.size)
            depth0 = max(cur["wl"] - ground, 0.3)
            ser[i_now - i0:] = ground + depth0 * np.clip(ratio, 0.2, 5) ** 0.6
        out[k] = ser                                                   # ระดับน้ำ ม.รทก. (ใช้เป็นระดับผิวน้ำของ cell แม่น้ำ)
    return out


def _runoff(P, cn2, bucket, loss=0.25):
    """SCS-CN ต่อเนื่อง (event/AMC) + คันนา (bucket มม.) ต่อ cell, float32 เพื่อประหยัดหน่วยความจำ"""
    from model import _cn_amc
    T, N = P.shape
    pcum = np.zeros(N, np.float32); qcum = np.zeros(N, np.float32); dry = np.full(N, 99, np.float32)
    cn_ev = cn2.copy(); S = 25400 / cn_ev - 254; B = 0.5 * bucket
    cs = np.cumsum(P, axis=0, dtype=np.float32)
    out = np.zeros((T, N), np.float32)
    for t in range(T):
        p = P[t]
        start = (p > 0.1) & (dry >= 12)
        if start.any():
            p5 = cs[t] - (cs[t - 120] if t >= 120 else 0)
            cn_ev = np.where(start, _cn_amc(cn2, p5), cn_ev).astype(np.float32)
            S = np.where(start, 25400 / cn_ev - 254, S); pcum = np.where(start, 0, pcum); qcum = np.where(start, 0, qcum)
        dry = np.where(p > 0.1, 0, dry + 1)
        pcum = pcum + p; ia = 0.2 * S
        qn = np.where(pcum > ia, (pcum - ia) ** 2 / (pcum - ia + S), 0).astype(np.float32)
        qi = np.maximum(qn - qcum, 0); qcum = qn
        over = np.maximum(qi - (bucket - B), 0)
        B = np.maximum(B + qi - over - np.where(p > 2, 0, loss), 0); out[t] = over
    return out


def main(site, only=None, hecras=False):
    t0 = time.time()
    st_dir = os.path.join(site, "data", "static"); lv = os.path.join(site, "data", "live")
    hs_dir = os.path.join(st_dir, "hotspots"); out_dir = os.path.join(lv, "hotspots"); os.makedirs(out_dir, exist_ok=True)
    idx = json.load(open(os.path.join(hs_dir, "hotspots.json"), encoding="utf8"))
    cache = json.load(open(os.path.join(lv, "rain_cache.json")))
    hexst = json.load(open(os.path.join(lv, "hex_state.json")))
    meta_live = json.load(open(os.path.join(lv, "meta.json"), encoding="utf8"))
    stations = json.load(open(os.path.join(lv, "stations.json"), encoding="utf8"))["level"]
    upj = os.path.join(lv, "upstream.json")
    rivers = json.load(open(upj, encoding="utf8"))["rivers"] if os.path.exists(upj) else []
    hp = os.path.join(lv, "gauges_hist.json"); hist = json.load(open(hp, encoding="utf8")) if os.path.exists(hp) else {}
    times = np.array(cache["t"], np.int64); Pg = np.array(cache["P"], np.float32) / 10.0; gids = np.array(cache["gids"])
    glon = 99.0 + 0.2 * (gids % 16); glat = 15.0 + 0.2 * (gids // 16)
    i_now = int(np.searchsorted(times, meta_live["now"]))
    i0 = int(np.searchsorted(times, hexst["t0"]))
    i1 = min(len(times) - 1, i_now + FC_H)
    T = i1 - i0 + 1
    wse0 = np.array(hexst["wse"]); free0 = np.array(hexst["free"])
    live = {}
    for hid_, m in idx.items():
        if only and hid_ not in only:
            continue
        d = np.load(os.path.join(hs_dir, f"{hid_}.npz"))
        dom = {k: d[k] for k in d.files if k != "meta"}
        ny, nx = dom["dem"].shape
        # rain per cell (IDW from 0.2° lattice) + SCS-CN with paddy bucket -> effective rain
        (sw, ne) = m["bounds"]
        la = np.linspace(ne[0], sw[0], ny); lo = np.linspace(sw[1], ne[1], nx)
        LA, LO = np.meshgrid(la, lo, indexing="ij")
        dd = np.hypot((LO.ravel()[:, None] - glon[None]) * 107.1, (LA.ravel()[:, None] - glat[None]) * 110.6)
        nn = np.argsort(dd, 1)[:, :4]; w = 1 / np.maximum(np.take_along_axis(dd, nn, 1), 0.5) ** 2; w /= w.sum(1, keepdims=True)
        iw = max(i0 - 120, 0)                                               # อุ่นเครื่อง 5 วันสำหรับ AMC/คันนา
        Pc = np.stack([(Pg[t][nn] * w).sum(1) for t in range(iw, i1 + 1)]).astype(np.float32)   # [Tw, cells]
        qe = _runoff(Pc, dom["cn"].ravel().astype(np.float32), np.where(dom["crop"].ravel(), 100.0, 10.0).astype(np.float32))
        qe = qe[i0 - iw:]; Pc = Pc[i0 - iw:]
        rain_eff = qe.reshape(T, ny, nx)
        # initial water from hex model state at t0: fill cells below the hex water surface
        hx = dom["hex"]; ok = hx >= 0
        h0 = np.zeros((ny, nx), np.float32)
        wet_hex = free0 > 1.0
        hh = np.where(ok, wse0[np.maximum(hx, 0)] - dom["dem"], 0)
        h0 = np.where(ok & wet_hex[np.maximum(hx, 0)], np.clip(hh, 0, 3), 0).astype(np.float32)
        # อนุรักษ์ปริมาตร: น้ำเริ่มต้นใน hex ไม่เกินปริมาตรน้ำอิสระของ hex นั้นจากโมเดล hex
        cell_a = m["dx"] ** 2
        vol_c = np.bincount(hx[ok], weights=h0[ok] * cell_a, minlength=len(free0))
        area_c = np.bincount(hx[ok], minlength=len(free0)) * cell_a
        target = free0 / 1000.0 * area_c
        sc = np.where(vol_c > 0, np.minimum(target / np.maximum(vol_c, 1e-9), 1.0), 0)
        h0 = np.where(ok, h0 * sc[np.maximum(hx, 0)], 0).astype(np.float32)
        stage = _stage_series(m, stations, rivers, hist, times, i0, i_now, T)
        # ระดับผิวน้ำตามแนวลำน้ำ: ความลึกน้ำเหนือท้องน้ำของสถานี (WL - z_ref) interpolate (IDW p=2, 2 สถานีใกล้สุด)
        # แล้วบวกกับท้องน้ำของแต่ละ cell (zbed = ค่าต่ำสุดของ DEM 30 ม. ใน cell) -> ผิวน้ำลาดตามท้องน้ำจริง
        # ตลิ่ง: น้ำล้นขึ้นที่ราบได้เมื่อความลึกเกินความสูงตลิ่ง (min_bank - z_ref) ที่ interpolate แบบเดียวกัน
        rr, cc = np.where(dom["river"] >= 0)
        by = {x["code"]: x for x in stations}
        ks = [k for k in sorted(stage)
              if by.get(m["stations"][k]["code"], {}).get("bank", -1e9) > m["stations"][k]["z_ref"]]
        zbed = dom["zbed"] if "zbed" in dom else dom["dem"]
        if rr.size and ks:
            rlat = la[rr]; rlon = lo[cc]
            slat = np.array([m["stations"][k]["lat"] for k in ks]); slon = np.array([m["stations"][k]["lon"] for k in ks])
            dk = np.hypot((rlon[:, None] - slon[None]) * 107.1, (rlat[:, None] - slat[None]) * 110.6)
            nn2 = np.argsort(dk, 1)[:, :min(2, len(ks))]
            wk = 1 / np.maximum(np.take_along_axis(dk, nn2, 1), 0.3) ** 2; wk /= wk.sum(1, keepdims=True)
            D_ = np.array([np.maximum(stage[k] - m["stations"][k]["z_ref"], 0) for k in ks])     # [K, T] ความลึกเหนือท้องน้ำ
            BH = np.array([by[m["stations"][k]["code"]]["bank"] - m["stations"][k]["z_ref"] for k in ks])
            zb_r = zbed[rr, cc].astype(np.float32)
            stage_fn = lambda hr: zb_r + (D_[nn2, min(hr, T - 1)] * wk).sum(1)
            river_bank = zb_r + (BH[nn2] * wk).sum(1)
        else:
            stage_fn = lambda hr: {}; river_bank = None
        # ปริมาณน้ำล้นตลิ่งที่อนุญาต = Σ max(Q - ความจุ, 0) ของแม่น้ำที่มีสถานีในโดเมนนี้ (ถ้ามีข้อมูล Q)
        codes = {x["code"] for x in m["stations"]}
        reach_codes = {key: set(c) for key, (_, g, c, _) in upm.REACHES.items()}
        fl = []
        for rv in rivers or []:
            if rv.get("q_obs") is None or not rv.get("qmax") or not (reach_codes.get(rv["key"], set()) & codes):
                continue
            tt_ = np.array(rv["t"]); qf = np.array([np.nan if v is None else v for v in rv["q_fc"]], float)
            qf = np.where(np.isnan(qf), rv["q_obs"], qf)
            fl.append(np.maximum(np.interp(times[i0:i1 + 1], tt_, qf) - rv["qmax"], 0))
        ovs = np.sum(fl, axis=0) if fl else None
        overflow_fn = (lambda hr: float(ovs[min(hr, T - 1)])) if ovs is not None else None
        snaps = (i_now - i0, i_now - i0 + 24, i_now - i0 + 48, T - 1)
        tt = time.time()
        r = model2d.run2d(dom, m, rain_eff, T - 1, stage_fn, h0=h0, snap_hours=snaps, river_bank=river_bank, overflow_fn=overflow_fn)
        # max over the forecast window only
        names = {}
        for lab, hr in zip(("now", "p24", "p48", "p72"), snaps):
            if hr in r["snaps"]:
                fn = f"{hid_}_{lab}.png"; model2d.depth_png(os.path.join(out_dir, fn), r["snaps"][hr], dom["river"]); names[lab] = fn
        hm = np.maximum.reduce([r["snaps"][h] for h in snaps if h in r["snaps"]] + [r["h"]])
        model2d.depth_png(os.path.join(out_dir, f"{hid_}_max72.png"), hm, dom["river"]); names["max72"] = f"{hid_}_max72.png"
        ser = r["series"]; dx = m["dx"]
        riv = dom["river"] >= 0
        def km2(h, thr=0.10):
            return round(float(((h >= thr) & dom["valid"] & ~riv).sum() * dx * dx / 1e6), 2)
        # per-hex flooded fraction (for calibration against the hex model)
        hex_ids = hx[ok]
        def hexfrac(h):
            wet = ((h >= 0.10) & ~riv)[ok]
            cnt = np.bincount(hex_ids, minlength=len(wse0)); wc_ = np.bincount(hex_ids, weights=wet, minlength=len(wse0))
            sel = np.where(cnt > 0)[0]
            return sel.tolist(), np.round(wc_[sel] / cnt[sel], 3).tolist(), np.round(np.minimum(cnt[sel] * dx * dx / 1e6 / hk_, 1), 3).tolist()
        hk_ = 1.0
        hsel, fnow, hcov = hexfrac(r["snaps"].get(snaps[0], r["h"]))
        _, fmax, _ = hexfrac(hm)
        live[hid_] = {"name": m["name"], "bounds": m["bounds"], "png": names, "dx_m": dx,
                      "t0": int(times[i0]), "now": int(times[i_now]), "t_end": int(times[i1]),
                      "area_now_km2": km2(r["snaps"].get(snaps[0], r["h"])), "area_max72_km2": km2(hm),
                      "series": [[int(times[i0] + 3600 * s_[0]), s_[1], round(s_[2], 3)] for s_ in ser],
                      "hex": hsel, "hexcov": hcov, "hexfrac_now": fnow, "hexfrac_max72": fmax,
                      "stations": [s["code"] for s in m["stations"]], "runtime_s": round(time.time() - tt, 1)}
        print(hid_, live[hid_]["area_now_km2"], live[hid_]["area_max72_km2"], live[hid_]["runtime_s"], "s", flush=True)
        if hecras:
            hd = os.path.join(site, "hecras", hid_); os.makedirs(hd, exist_ok=True)
            with open(os.path.join(hd, "rain.csv"), "w", encoding="utf-8") as f:
                f.write("datetime_utc,rain_mm_per_hr\n")
                for t_, v in zip(times[i0:i1 + 1], Pc.mean(1)):
                    f.write(f"{datetime.fromtimestamp(int(t_), timezone.utc).strftime('%Y-%m-%d %H:%M')},{v:.2f}\n")
            for k, s in enumerate(m["stations"]):
                if k in stage:
                    with open(os.path.join(hd, f"stage_{s['code'].replace('.', '')}.csv"), "w", encoding="utf-8") as f:
                        f.write("datetime_utc,stage_msl_m\n")
                        for t_, v in zip(times[i0:i1 + 1], stage[k]):
                            f.write(f"{datetime.fromtimestamp(int(t_), timezone.utc).strftime('%Y-%m-%d %H:%M')},{v:.2f}\n")
    old = {}
    lp = os.path.join(lv, "hotspots_live.json")
    if os.path.exists(lp):
        try:
            old = json.load(open(lp, encoding="utf8"))
        except Exception:  # noqa
            old = {}
    old.update(live)
    old["_generated"] = datetime.now(TZ).isoformat(timespec="seconds")
    json.dump(old, open(lp, "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))
    print("hotspots done", round(time.time() - t0, 1), "s")
    return old


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="."); ap.add_argument("--only", nargs="*"); ap.add_argument("--hecras", action="store_true")
    a = ap.parse_args()
    main(a.site, a.only, a.hecras)
