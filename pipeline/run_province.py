# -*- coding: utf-8 -*-
"""
พยากรณ์น้ำท่วม 2D ทั้งจังหวัด 72 ชม. (โดเมน prov_ev 300 ม.) — รันใน GitHub Actions ทุก 6 ชม. ต่อจาก run_hotspots.py

  python pipeline/run_province.py --site .

ใช้ข้อมูลรอบล่าสุดของ run_update.py (rain_cache, hex_state, stations, upstream, gauges_hist) เหมือน run_hotspots.py
แต่ใช้ชุดพารามิเตอร์ที่สอบเทียบกับ Sentinel-1 ทั้งจังหวัด (event_2d.py รอบ 5):
  - cell แม่น้ำใช้ระดับน้ำเฉพาะสถานีในลำน้ำเดียวกัน (น่าน–ยม / ปิง / เจ้าพระยา / แม่วงก์–สะแกกรัง)
  - ตลิ่งใช้งานและความจุน้ำเข้าทุ่งรายลำน้ำ ; ความจุบึงบอระเพ็ดตามข้อมูล ชป. ; คันบึงเก็บน้ำถึง +24.00 ม.
  - ขอบเหนือ (ที่ราบยม–น่าน) รับน้ำบ่าตามระดับ Y.5
ระดับบึงเริ่มต้น: ระดับ ณ "ตอนนี้" ของรอบก่อน (data/live/prov_state.json) ; ไม่มี -> ค่าตามเดือน (BUENG_MONTH)
ผล: data/live/hotspots/prov_{now,p24,p48,p72,max72}.png + รายการ "prov" ใน hotspots_live.json (แท็บ 2D บนเว็บ ; calibrate.py ไม่ใช้)
"""
import argparse, json, math, os, sys, time
from datetime import datetime, timezone, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model2d, event_2d  # noqa: E402
from run_hotspots import _idw4, _runoff, _stage_series, FC_H  # noqa: E402

TZ = timezone(timedelta(hours=7))
P = dict(bank_off={"nan": -1.5, "ping": -1.5, "cpy": -2.0, "skg": -1.0}, loss=0.08, n_mult=2.0, hold_level=24.0,
         in_cap={"nan": 500, "ping": 1000, "cpy": 1000, "skg": 300})
BUENG_MONTH = {1: 23.5, 2: 23.0, 3: 22.5, 4: 22.0, 5: 21.8, 6: 21.8, 7: 21.8, 8: 22.0, 9: 23.0, 10: 24.0, 11: 24.0, 12: 24.0}


def main(site):
    t_start = time.time()
    st_dir = os.path.join(site, "data", "static"); lv = os.path.join(site, "data", "live")
    hs_dir = os.path.join(st_dir, "hotspots"); out_dir = os.path.join(lv, "hotspots"); os.makedirs(out_dir, exist_ok=True)
    d = np.load(os.path.join(hs_dir, "prov_ev.npz")); dom = {k: d[k] for k in d.files if k != "meta"}; m = json.loads(str(d["meta"]))
    ny, nx = dom["dem"].shape; dx = float(m["dx"])
    cache = json.load(open(os.path.join(lv, "rain_cache.json")))
    hexst = json.load(open(os.path.join(lv, "hex_state.json")))
    meta_live = json.load(open(os.path.join(lv, "meta.json"), encoding="utf8"))
    stations = json.load(open(os.path.join(lv, "stations.json"), encoding="utf8"))["level"]
    upj = os.path.join(lv, "upstream.json")
    rivers = json.load(open(upj, encoding="utf8"))["rivers"] if os.path.exists(upj) else []
    hp = os.path.join(lv, "gauges_hist.json"); hist = json.load(open(hp, encoding="utf8")) if os.path.exists(hp) else {}
    times = np.array(cache["t"], np.int64); Pg = np.array(cache["P"], np.float32) / 10.0; gids = np.array(cache["gids"])
    glon = 99.0 + 0.2 * (gids % 16); glat = 15.0 + 0.2 * (gids // 16)
    i_now = int(np.searchsorted(times, meta_live["now"])); i0 = int(np.searchsorted(times, hexst["t0"]))
    i1 = min(len(times) - 1, i_now + FC_H); T = i1 - i0 + 1
    # ---- ฝน -> น้ำท่า (อุ่นเครื่อง 5 วัน) — คำนวณที่จุด lattice ต่อชนิดพื้นที่ แล้ว IDW (ประหยัดหน่วยความจำ)
    (sw, ne) = m["bounds"]; la = np.linspace(ne[0], sw[0], ny); lo = np.linspace(sw[1], ne[1], nx)
    LA, LO = np.meshgrid(la, lo, indexing="ij")
    iw = max(i0 - 120, 0)
    cn = np.where(dom["cn"] > 0, dom["cn"], 80).ravel().astype(np.float32)
    bk = np.where(dom["crop"].ravel(), 100.0, 10.0).astype(np.float32)
    key = cn * 1000 + bk; uk, inv = np.unique(key, return_inverse=True); U = uk.size
    Pw = Pg[iw:i1 + 1]                                                   # [Tw, gpts]
    npt = Pw.shape[1]
    Q = _runoff(np.repeat(Pw, U, axis=1), np.tile((uk // 1000).astype(np.float32), npt), np.tile((uk % 1000).astype(np.float32), npt))
    Q = Q.reshape(Q.shape[0], npt, U)[i0 - iw:]
    nn, w = _idw4(LA, LO, glat, glon)
    rain_eff = event_2d.LazyRain(Q, nn, w, inv, (ny, nx))
    # ---- สถานี: เพิ่มสถานีที่ไม่อยู่ใน meta ของโดเมน (C.13, PIN005) ด้วย z_ref จาก DEM รอบสถานี
    by = {s["code"]: s for s in stations}
    meta2 = dict(m); stl = list(m["stations"])
    have = {s["code"] for s in stl}
    for c in event_2d.USE_BY_HID["prov_ev"]:
        if c in have or c not in by:
            continue
        e = by[c]
        X_ = math.radians(e["lon"]) * 6378137.0; Y_ = math.log(math.tan(math.pi / 4 + math.radians(e["lat"]) / 2)) * 6378137.0
        ex = m["extent_webm"]; cw = m["cell_webm"]; ci_, ri_ = int((X_ - ex[0]) // cw), int((ex[3] - Y_) // cw)
        if 0 <= ri_ < ny and 0 <= ci_ < nx:
            win = dom["dem"][max(ri_ - 1, 0):ri_ + 2, max(ci_ - 1, 0):ci_ + 2]
            stl.append({"code": c, "name": e["name"], "lat": e["lat"], "lon": e["lon"], "z_ref": round(float(win[win > 0].min()), 2)})
    meta2["stations"] = stl
    stage = _stage_series(meta2, stations, rivers, hist, times, i0, i_now, T)          # {index: ระดับน้ำ ม.รทก. [T]}
    use = [k for k in sorted(stage) if stl[k]["code"] in event_2d.USE_BY_HID["prov_ev"] and by.get(stl[k]["code"], {}).get("bank", -1e9) > stl[k]["z_ref"]]
    codes = [stl[k]["code"] for k in use]
    G = event_2d.GROUPS; GO = event_2d.GROUP_OF
    # กลุ่มลำน้ำราย cell (จากสถานีใกล้สุดตอนสร้างโดเมน) ; ลำน้ำที่ไม่มีสถานีรายงาน -> พื้นดิน
    rv = dom["river"]; g_cell = np.full(rv.shape, -1, np.int16)
    for k_, s_ in enumerate(m["stations"]):
        g_cell[rv == k_] = G.index(GO.get(s_["code"], "cpy"))
    haveg = {G.index(GO.get(c, "cpy")) for c in codes}
    river = np.where(np.isin(g_cell, list(haveg)), rv, -1).astype(rv.dtype)
    rr, cc = np.where(river >= 0)
    zbed = (dom["zbed"] if "zbed" in dom else dom["dem"]).astype(np.float32)
    zb = zbed[rr, cc]
    slat = np.array([stl[k]["lat"] for k in use]); slon = np.array([stl[k]["lon"] for k in use])
    dk = np.hypot((lo[cc][:, None] - slon[None]) * 107.1, (la[rr][:, None] - slat[None]) * 110.6)
    sg = np.array([G.index(GO.get(c, "cpy")) for c in codes])
    dk = dk + 1e4 * (g_cell[rr, cc][:, None] != sg[None])
    nn2 = np.argsort(dk, 1)[:, :2]
    wk = 1 / np.maximum(np.take_along_axis(dk, nn2, 1), 0.3) ** 2; wk /= wk.sum(1, keepdims=True)
    D = np.array([np.maximum(stage[k] - stl[k]["z_ref"], 0) for k in use])
    BH = np.array([max(by[stl[k]["code"]]["bank"] - stl[k]["z_ref"], 0.5) for k in use])
    boff = np.array([P["bank_off"].get(G[g], -1.0) for g in g_cell[rr, cc]], np.float32)
    bank_r = zb + (BH[nn2] * wk).sum(1) + boff
    # ขอบเหนือ: Y.5
    iy5 = next((k for k in use if stl[k]["code"] == "Y.5"), None)
    edge = np.zeros(river.shape, bool)
    if iy5 is not None:
        edge[:2, :] = True
        edge &= (LO >= event_2d.NORTH_LON[0]) & (LO <= event_2d.NORTH_LON[1]) & dom["valid"].astype(bool) & (river < 0)
        river = river.copy(); river[edge] = 0
    rr2, cc2 = np.where(river >= 0)
    pos = np.full(river.shape, -1, np.int32); pos[rr, cc] = np.arange(rr.size)
    is_r = pos[rr2, cc2] >= 0; idx_r = pos[rr2, cc2]
    z_edge = dom["dem"][rr2, cc2].astype(np.float32)

    def stage_fn(hr):
        h_ = min(hr, T - 1); s = np.empty(rr2.size, np.float32)
        s[is_r] = (zb + (D[nn2, h_] * wk).sum(1))[idx_r[is_r]]
        if (~is_r).any():
            s[~is_r] = np.maximum(stage[iy5][h_], z_edge[~is_r])
        return s
    bank = np.empty(rr2.size, np.float32); bank[is_r] = bank_r[idx_r[is_r]]; bank[~is_r] = z_edge[~is_r]
    grp = np.full(river.shape, -1, np.int32); grp[rr, cc] = g_cell[rr, cc]; grp[edge] = G.index("nan")
    caps = np.array([float(P["in_cap"].get(g, 1e9)) for g in G], np.float32)
    dom2 = dict(dom); dom2["river"] = river
    dom2["n"] = np.where(dom["river"] >= 0, dom["n"], dom["n"] * P["n_mult"]).astype(np.float32)
    # ---- บึงบอระเพ็ด
    fs = [event_2d.poly_mask(json.load(open(os.path.join(hs_dir, f), encoding="utf8"))["features"], LA, LO)
          for f in ("thatako_contrib.geojson", "thatako_aoi.geojson")]
    hold = (fs[0] | fs[1]) & (dom["river"] < 0)
    dem_b, lake = event_2d.burn_boraphet(dom["dem"].astype(np.float32), hold, LA, LO, dx, log=lambda *a: None)
    dom2["dem"] = dem_b; dom2["zbed"] = np.minimum(zbed, dem_b)
    sp = os.path.join(lv, "prov_state.json")
    lv0 = None
    if os.path.exists(sp):
        try:
            ps = json.load(open(sp))
            if abs(ps["t"] - int(times[i0])) < 5 * 86400:
                lv0 = float(ps["lake_level"])
        except Exception:  # noqa
            lv0 = None
    src = "รอบก่อน"
    if lv0 is None:
        lv0 = BUENG_MONTH[datetime.fromtimestamp(int(times[i0]), TZ).month]; src = "ค่าตามเดือน"
    # ---- น้ำเริ่มต้น: ผิวน้ำราย hex จากโมเดล hex (เหมือน run_hotspots) + บึง
    wse0 = np.array(hexst["wse"]); free0 = np.array(hexst["free"])
    hx = dom["hex"]; ok = hx >= 0
    hh = np.where(ok, wse0[np.maximum(hx, 0)] - dem_b, 0)
    h0 = np.where(ok & (free0 > 1.0)[np.maximum(hx, 0)], np.clip(hh, 0, 3), 0).astype(np.float32)
    ca = dx * dx
    vol_c = np.bincount(hx[ok], weights=h0[ok] * ca, minlength=len(free0)); area_c = np.bincount(hx[ok], minlength=len(free0)) * ca
    sc = np.where(vol_c > 0, np.minimum(free0 / 1000.0 * area_c / np.maximum(vol_c, 1e-9), 1.0), 0)
    h0 = np.where(ok, h0 * sc[np.maximum(hx, 0)], 0).astype(np.float32)
    h0 = np.where(lake, np.maximum(lv0 - dem_b, 0), h0).astype(np.float32)
    snaps = (i_now - i0, i_now - i0 + 24, i_now - i0 + 48, T - 1)
    zones = np.where(np.isclose(dom["n"], 0.03) | lake, -1, dom["aoi"]).astype(np.int16)   # พื้นที่ท่วมรายตำบลไม่รวมบึง/แหล่งน้ำถาวร
    tt = time.time()
    r = model2d.run2d(dom2, m, rain_eff, T - 1, stage_fn, h0=h0, loss_mmh=P["loss"], snap_hours=snaps, river_bank=bank,
                      overflow_fn=lambda hr: caps, zones=zones, n_zones=len(m["aoi"]), hold_mask=hold, hold_level=P["hold_level"],
                      river_grp=grp)
    rt = round(time.time() - tt, 1)
    riv0 = (dom["river"] >= 0) | (dom["aoi"] < 0)                         # ภาพแสดงเฉพาะในเขตจังหวัด (นอกจังหวัด = โปร่งใส)
    names = {}
    for lab, hr in zip(("now", "p24", "p48", "p72"), snaps):
        if hr in r["snaps"]:
            fn = f"prov_{lab}.png"; model2d.depth_png(os.path.join(out_dir, fn), r["snaps"][hr], riv0); names[lab] = fn
    hm = np.maximum.reduce([r["snaps"][h] for h in snaps if h in r["snaps"]] + [r["h"]])
    model2d.depth_png(os.path.join(out_dir, "prov_max72.png"), hm, riv0); names["max72"] = "prov_max72.png"
    from run_hotspots import _patches
    perm = np.isclose(dom["n"], 0.03) | lake                               # แหล่งน้ำถาวร (WorldCover) + บึงบอระเพ็ด ไม่นับเป็นน้ำท่วม
    km2 = lambda h: round(float(_patches((h >= 0.10) & dom["valid"].astype(bool) & ~riv0 & ~perm, 0.25, ca / 1e6).sum() * ca / 1e6), 1)
    if os.environ.get("PROV_DEBUG"):
        np.savez_compressed(os.environ["PROV_DEBUG"], h_now=r["snaps"].get(snaps[0], r["h"]).astype(np.float16), h0=h0.astype(np.float16), perm=perm)
    h_now = r["snaps"].get(snaps[0], r["h"])
    lake_lv = float(np.median((dem_b + h_now)[lake & (h_now > 0.05)])) if (lake & (h_now > 0.05)).any() else lv0
    zs = r["zseries"]; zn = zs[i_now - i0] if len(zs) > i_now - i0 else None
    zmax = zs[i_now - i0:].max(0) if len(zs) > i_now - i0 else None
    zt = zs.sum(1) if len(zs) else np.zeros(0)                         # พื้นที่ท่วม ≥ 10 ซม. ในเขตจังหวัด (ไม่รวมบึง/แหล่งน้ำถาวร) รายชั่วโมง
    ser = [[int(times[i0] + 3600 * s_[0]), round(float(zt[s_[0]]), 1) if s_[0] < len(zt) else s_[1], round(s_[2], 3)] for s_ in r["series"] if s_[0] <= T - 1]
    entry = {"name": "ทั้งจังหวัด (300 ม.)", "bounds": m["bounds"], "png": names, "dx_m": dx, "no_calib": True,
             "t0": int(times[i0]), "now": int(times[i_now]), "t_end": int(times[i1]),
             "area_now_km2": km2(h_now), "area_max72_km2": km2(hm), "series": ser,
             "stations": codes, "runtime_s": rt, "bueng_level_now": round(lake_lv, 2), "bueng_level_init": round(lv0, 2), "bueng_init_src": src,
             "aoi": "data/static/province_tambon.geojson",
             "aoi_now_km2": [round(float(v), 2) for v in zn] if zn is not None else None,
             "aoi_max72_km2": [round(float(v), 2) for v in zmax] if zmax is not None else None,
             "tambon": [{"name": a["name"], "district": a["district"]} for a in m["aoi"]]}
    lp = os.path.join(lv, "hotspots_live.json")
    old = json.load(open(lp, encoding="utf8")) if os.path.exists(lp) else {}
    old["prov"] = entry; old["_generated"] = datetime.now(TZ).isoformat(timespec="seconds")
    json.dump(old, open(lp, "w", encoding="utf8"), ensure_ascii=False, separators=(",", ":"))
    json.dump({"t": int(times[i_now]), "lake_level": round(lake_lv, 3)}, open(sp, "w"))
    print("prov", entry["area_now_km2"], entry["area_max72_km2"], "bueng", entry["bueng_level_now"], "model", rt, "s total", round(time.time() - t_start, 1), "s", flush=True)
    return entry


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--site", default=".")
    main(ap.parse_args().site)
