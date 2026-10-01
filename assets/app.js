/* Nakhon Sawan Flood Watch — web client (Leaflet, no build step) */
(() => {
  "use strict";
  const REFRESH_MS = 5 * 60 * 1000;
  const CLS_COL = ["rgba(0,0,0,0)", "#bfdbfe", "#60a5fa", "#2563eb", "#1e3a8a"];
  const CLS_LBL = ["ไม่ท่วม (<10 ซม.)", "เฝ้าระวัง 10–25 ซม.", "ท่วมขังเล็กน้อย 25–50 ซม.", "ท่วมขังปานกลาง 50–100 ซม.", "ท่วมสูง >100 ซม."];
  const CLS_SHORT = ["ไม่ท่วม", "เฝ้าระวัง", "ท่วมขังเล็กน้อย", "ท่วมขังปานกลาง", "ท่วมสูง"];
  const RAIN_BR = [1, 10, 35, 90, 150];           // มม. (เกณฑ์กรมอุตุฯ: เล็กน้อย/ปานกลาง/หนัก/หนักมาก)
  const RAIN_COL = ["rgba(0,0,0,0)", "#d9f99d", "#4ade80", "#facc15", "#f97316", "#b91c1c"];
  const DUR_BR = [1, 12, 24, 72, 168];            // ชั่วโมง
  const DUR_COL = ["rgba(0,0,0,0)", "#fde68a", "#fbbf24", "#f97316", "#dc2626", "#7f1d1d"];
  const REM_COL = ["#86efac", "#fde047", "#fb923c", "#dc2626", "#7f1d1d"], REM_LBL = ["< 1 วัน", "1–3 วัน", "3–7 วัน", "7–14 วัน", "> 14 วัน"];
  const WL_COL = { 1: "#b45309", 2: "#eab308", 3: "#16a34a", 4: "#2563eb", 5: "#dc2626" };
  const WL_LBL = { 1: "น้อยวิกฤต", 2: "น้อย", 3: "ปกติ", 4: "มาก", 5: "ล้นตลิ่ง" };
  const SRC_LBL = ["–", "น้ำฝนท่วมขัง", "น้ำล้นตลิ่ง/น้ำจากต้นน้ำ", "ฝน + ล้นตลิ่ง"];
  const PROV_COL = { 62: "#0d9488", 65: "#7c3aed", 66: "#ea580c", 67: "#db2777" };

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const fmtT = (unix, withDay = true) => new Date(unix * 1000).toLocaleString("th-TH",
    { timeZone: "Asia/Bangkok", ...(withDay ? { day: "numeric", month: "short" } : {}), hour: "2-digit", minute: "2-digit" });
  const nf = (v, d = 0) => (v == null || v < 0) ? "–" : Number(v).toLocaleString("th-TH", { maximumFractionDigits: d, minimumFractionDigits: d });
  const binCol = (v, br, cols) => { let i = 0; while (i < br.length && v >= br[i]) i++; return cols[i]; };
  const durTxt = h => h == null || h < 0 ? "–" : h >= 999 ? "> 14 วัน" : h >= 48 ? `${Math.round(h / 24)} วัน` : `${h} ชม.`;

  const S = { params: null, status: null, frames: null, meta: null, stations: null, districts: null, series: null,
    hexLayer: null, frame: 0, mode: "class", playing: null, ver: null };

  // ---------------------------------------------------------------- map
  const map = L.map("map", { zoomControl: true, preferCanvas: true, minZoom: 7 }).setView([15.72, 100.0], 9);
  const base = {
    "แผนที่เทาอ่อน (Esri)": L.layerGroup([
      L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        { attribution: "Esri, HERE, Garmin, © OpenStreetMap contributors", maxZoom: 19, maxNativeZoom: 16 }),
      L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
        { maxZoom: 19, maxNativeZoom: 16, pane: "shadowPane" })]),
    "OpenStreetMap": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap", maxZoom: 19 }),
    "ภาพถ่ายดาวเทียม (Esri)": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Esri, Maxar, Earthstar Geographics", maxZoom: 19 }),
  };
  base["แผนที่เทาอ่อน (Esri)"].addTo(map);
  const renderer = L.canvas({ padding: 0.3 });
  const overlays = {};
  const layerCtl = L.control.layers(base, overlays, { collapsed: true, position: "topright" }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);

  // ---------------------------------------------------------------- data
  const j = (p, v) => fetch(`${p}?v=${v || Date.now()}`, { cache: "no-store" }).then(r => { if (!r.ok) throw new Error(p); return r.json(); });
  const jOpt = (p, v) => j(p, v).catch(() => null);

  async function loadStatic() {
    const [hex, params, prov, dist, susc] = await Promise.all([
      j("data/static/hex.geojson", "s1"), j("data/static/params.json", "s1"),
      jOpt("data/static/province.geojson", "s1"), jOpt("data/static/districts.geojson", "s1"),
      jOpt("data/static/susceptibility.json", "s1")]);
    S.params = params;
    S.hexLayer = L.geoJSON(hex, { renderer, style: () => ({ weight: 0, fillOpacity: 0 }), onEachFeature: (f, l) => l.on("click", e => S.view === "simple" ? simplePopup(f.properties.i, e.latlng) : hexPopup(f.properties.i, e.latlng)) });
    S.hexLayer.addTo(map);
    layerCtl.addOverlay(S.hexLayer, "ผลวิเคราะห์ราย hex (1 กม²)");
    if (dist) {
      S.distLayer = L.geoJSON(dist, { renderer, interactive: false, style: { color: "#475569", weight: 1, fill: false, opacity: .7, dashArray: "3 3" } }).addTo(map);
      layerCtl.addOverlay(S.distLayer, "ขอบเขตอำเภอ");
    }
    if (prov) {
      S.provLayer = L.geoJSON(prov, { renderer, interactive: false, style: { color: "#0f172a", weight: 2, fill: false } }).addTo(map);
      map.fitBounds(S.provLayer.getBounds(), { padding: [10, 10] });
      layerCtl.addOverlay(S.provLayer, "ขอบเขตจังหวัด");
    }
    if (susc) {
      S.susc = L.imageOverlay("data/static/susceptibility.png", susc.bounds, { opacity: .55, interactive: false });
      layerCtl.addOverlay(S.susc, "พื้นที่ลุ่มต่ำ (HAND) — ArcGIS Pro");
      S.suscMeta = susc;
    }
  }

  async function loadLive(force) {
    const meta = await j("data/live/meta.json");
    if (!force && S.meta && meta.generated === S.meta.generated) { setLiveDot(meta); return; }
    const v = encodeURIComponent(meta.generated);
    const [status, frames, stations, districts, series] = await Promise.all([
      j("data/live/status.json", v), j("data/live/frames.json", v), jOpt("data/live/stations.json", v),
      jOpt("data/live/districts.json", v), jOpt("data/live/series.json", v)]);
    const [upstream, hot] = await Promise.all([jOpt("data/live/upstream.json", v), jOpt("data/live/hotspots_live.json", v)]);
    Object.assign(S, { meta, status, frames, stations, districts, series, upstream, hot });
    const nowIdx = frames.t.indexOf(frames.now);
    const sl = $("#slider"); sl.max = frames.t.length - 1;
    if (S.frame === 0 || force || !S.userMoved) S.frame = nowIdx;
    sl.value = S.frame;
    $("#tlStart").textContent = fmtT(frames.t[0]); $("#tlEnd").textContent = fmtT(frames.t[frames.t.length - 1]);
    renderAll();
    if (meta.sources?.gistda?.ok) loadGistda(v);
    loadS1(v);
  }

  async function loadS1(v) {
    const [g, st] = await Promise.all([jOpt("data/live/s1_flood.geojson", v), jOpt("data/live/s1_status.json", v)]);
    S.s1status = st; renderAbout();
    if (!g || !g.features?.length) return;
    const when = g.properties?.t_acq ? new Date(g.properties.t_acq * 1000).toLocaleDateString("th-TH", { timeZone: "Asia/Bangkok", dateStyle: "medium" }) : "";
    const on = S.s1 && map.hasLayer(S.s1);
    if (S.s1) { map.removeLayer(S.s1); layerCtl.removeLayer(S.s1); }
    S.s1 = L.geoJSON(g, { renderer, style: f => ({ color: "#7c3aed", weight: .6, fillColor: "#8b5cf6", fillOpacity: .15 + .5 * Math.min((f.properties["ท่วม_%"] || 0) / 100, 1) }),
      onEachFeature: (f, l) => l.bindPopup(`<b>น้ำท่วมจาก Sentinel-1 (SAR)</b><br>${Object.entries(f.properties || {}).map(([k, x]) => `${k}: ${x}`).join("<br>")}<br><span class="note">แปลเองจากภาพเรดาร์ — ในเมือง/ใต้ต้นไม้อาจตรวจไม่พบ</span>`) });
    layerCtl.addOverlay(S.s1, `Sentinel-1 น้ำท่วม (${when})`);
    if (on) S.s1.addTo(map);
  }

  async function loadGistda(v) {
    const g = await jOpt("data/live/gistda_flood.geojson", v);
    if (!g) return;
    if (S.gistda) { map.removeLayer(S.gistda); layerCtl.removeLayer(S.gistda); }
    S.gistda = L.geoJSON(g, { renderer, style: { color: "#db2777", weight: 1, fillColor: "#f472b6", fillOpacity: .45 },
      onEachFeature: (f, l) => l.bindPopup(`<b>GISTDA พื้นที่น้ำท่วมจากดาวเทียม</b><br>${Object.entries(f.properties || {}).slice(0, 8).map(([k, x]) => `${k}: ${x}`).join("<br>")}`) });
    layerCtl.addOverlay(S.gistda, "GISTDA น้ำท่วมจากดาวเทียม (3 วัน)");
  }

  function setLiveDot(meta) {
    const age = (Date.now() - new Date(meta.generated).getTime()) / 60000;
    const d = $("#liveDot"); d.className = "dot " + (age < 150 ? "live" : "stale");
    $("#updated").textContent = `อัปเดต ${new Date(meta.generated).toLocaleString("th-TH", { timeZone: "Asia/Bangkok", dateStyle: "medium", timeStyle: "short" })} · ${age < 60 ? Math.round(age) + " นาทีที่แล้ว" : Math.round(age / 60) + " ชม.ที่แล้ว"}`;
  }

  // ---------------------------------------------------------------- render
  function renderAll() {
    setLiveDot(S.meta); renderKpis(); styleHex(); renderLegend(); renderTimelineLabel();
    renderDistricts(); renderStations(); renderRain(); renderUpstream(); renderHot(); renderAbout(); renderSimple();
  }

  function hexStyleFn() {
    const st = S.status, m = S.mode;
    const frameCls = S.frames.c[S.frame];
    const isNow = S.frames.t[S.frame] === S.frames.now;
    const fo = i => st.f && isNow ? .35 + .5 * Math.min(st.f[i], 100) / 100 : .78;     // โปร่งตามสัดส่วนพื้นที่ท่วมใน hex
    if (m === "hist") return () => ({ fillColor: "#000", fillOpacity: 0 });
    if (m === "class") return i => { const c = +frameCls[i]; return { fillColor: CLS_COL[c], fillOpacity: c ? fo(i) : 0 }; };
    if (m === "m72") return i => { const c = st.m72[i]; return { fillColor: CLS_COL[c], fillOpacity: c ? .78 : 0 }; };
    if (m === "u72") return i => { const d = (st.u72 || [])[i] || 0; const c = d >= 100 ? 4 : d >= 50 ? 3 : d >= 25 ? 2 : d >= 10 ? 1 : 0; return { fillColor: CLS_COL[c], fillOpacity: c ? .8 : 0 }; };
    if (m === "dur") return i => { const h = st.c[i] ? Math.max(st.h[i], 0) + Math.max(st.r[i] >= 999 ? 336 : st.r[i], 0) : 0; const col = binCol(h, DUR_BR, DUR_COL); return { fillColor: col, fillOpacity: h ? .8 : 0 }; };
    const arr = st[m]; return i => { const v = arr[i]; return { fillColor: binCol(v, RAIN_BR, RAIN_COL), fillOpacity: v >= 1 ? .7 : 0 }; };
  }
  function styleHex() {
    if (!S.hexLayer || !S.status) return;
    const f = hexStyleFn();
    S.hexLayer.eachLayer(l => { const s = f(l.feature.properties.i); l.setStyle({ weight: 0, fillColor: s.fillColor, fillOpacity: s.fillOpacity }); });
    $("#timeline").style.visibility = S.mode === "class" ? "visible" : "hidden";
  }

  function renderLegend() {
    if (!S.frames) return;
    if (S.view === "simple") { $("#legend").innerHTML = simpleLegend(); return; }
    const m = S.mode; let h = "";
    const row = (c, t) => `<div class="row"><span class="sw" style="background:${c}"></span>${t}</div>`;
    if (m === "class" || m === "m72" || m === "u72") { h = `<b>${m === "class" ? "ความลึกน้ำท่วมขัง (ประมาณ)" : m === "m72" ? "ความลึกสูงสุดใน 72 ชม." : "น้ำล้นตลิ่ง/ต้นน้ำ สูงสุด 72 ชม."}</b>` + CLS_LBL.slice(1).map((t, k) => row(CLS_COL[k + 1], t)).join(""); }
    else if (m === "hist") { h = histLegend(); }
    else if (m === "dur") { h = "<b>ระยะเวลาท่วมรวม (ผ่านมา+คาดการณ์)</b>" + ["1–12 ชม.", "12–24 ชม.", "1–3 วัน", "3–7 วัน", "> 7 วัน"].map((t, k) => row(DUR_COL[k + 1], t)).join(""); }
    else { h = `<b>${{ p24: "ฝน 24 ชม.", p7d: "ฝน 7 วัน", f72: "ฝนพยากรณ์ 72 ชม." }[m]} (มม.)</b>` + ["1–10", "10–35", "35–90", "90–150", "> 150"].map((t, k) => row(RAIN_COL[k + 1], t)).join(""); }
    if (S.pevLayer && map.hasLayer(S.pevLayer)) h += `<b style='margin-top:6px'>ท่วม ≥ 30 วัน ปี ${+S.pevY + 543}: จำลอง vs ดาวเทียม</b>` +
      [["#2563eb", "ตรงกัน"], ["#ea580c", "จริงแต่จำลองไม่ถึง"], ["#facc15", "จำลองเกิน"]].map(([c, t]) => row(c, t)).join("");
    if (S.suscMeta && map.hasLayer(S.susc)) h += "<b style='margin-top:6px'>พื้นที่ลุ่มต่ำ (HAND)</b>" + S.suscMeta.classes.map((t, k) => row(S.suscMeta.colors[k], t)).join("");
    $("#legend").innerHTML = h;
  }

  function renderKpis() {
    const s = S.meta.summary;
    const k = (v, u, l, hot) => `<div class="kpi${hot ? " hot" : ""}"><div class="v">${v}<small>${u}</small></div><div class="l">${l}</div></div>`;
    $("#kpis").innerHTML = k(nf(s.km2_now), "กม²", "ท่วมขัง ≥25 ซม. ตอนนี้", s.km2_now > 0) + k(nf(s.km2_watch), "กม²", "เฝ้าระวัง ≥10 ซม.") +
      k(nf(s.km2_72h), "กม²", "คาดท่วมขังใน 72 ชม.", s.km2_72h > s.km2_now) + k(nf(s.rain24_max), "มม.", "ฝน 24 ชม. สูงสุด (hex)") +
      (s.up_vol72_mcm != null ? k(nf(s.up_vol72_mcm, 1), "ล้าน ลบ.ม.", "น้ำจาก 4 จังหวัดต้นน้ำเข้า นว. ใน 72 ชม.") : k(nf(s.fc72_max), "มม.", "ฝนพยากรณ์ 72 ชม. สูงสุด")) + k(nf(s.wl_over_bank), "สถานี", "ระดับน้ำล้นตลิ่ง (นครสวรรค์)", s.wl_over_bank > 0);
  }

  function renderTimelineLabel() {
    const t = S.frames.t[S.frame], fc = t > S.frames.now;
    const dh = Math.round((t - S.frames.now) / 3600);
    const el = $("#tlLabel"); el.className = "tl-now" + (fc ? " fc" : "");
    el.textContent = `${fmtT(t)} · ${dh === 0 ? "ตอนนี้" : dh > 0 ? `พยากรณ์ +${dh} ชม.` : `${dh} ชม.`}`;
  }

  function renderDistricts() {
    const tb = $("#distTable tbody"); if (!S.districts) { tb.innerHTML = ""; return; }
    const bar = (a, tot) => tot > 0 && a ? `<div class="dbar">${a.map((v, k) => v > 0 ? `<i style="width:${v / tot * 100}%;background:${REM_COL[k]}" title="${REM_LBL[k]}: ${nf(v, 1)} กม²"></i>` : "").join("")}</div>` : "";
    const durCell = d => d.dur_km2 > 0 ? `<td class="dur" title="ท่วมมาแล้ว มัธยฐาน ${durTxt(d.h_med)} · นานสุด ${durTxt(d.h_max)}\nคาดลดใน มัธยฐาน ${durTxt(d.r_med)} · นานสุด ${durTxt(d.r_max)}">${durTxt(d.h_med)} → ${durTxt(d.r_med)}${bar(d.r_km2, d.dur_km2)}</td>` : `<td class="dur">–</td>`;
    tb.innerHTML = S.districts.map(d => `<tr data-a="${d.amphoe}"><td><span class="sw" style="background:${CLS_COL[d.max_cls_now] === CLS_COL[0] ? "#fff" : CLS_COL[d.max_cls_now]}"></span>${d.amphoe}</td><td>${nf(d.km2_now)}</td><td>${nf(d.km2_72h)}</td><td>${nf(d.rain24, 1)}</td>${durCell(d)}</tr>`).join("");
    const du = S.meta?.summary?.duration;
    $("#durSum").innerHTML = du && du.dur_km2 > 0 ? `ทั้งจังหวัด: ลึก ≥ 10 ซม. <b>${nf(du.dur_km2, 1)}</b> กม² · ท่วมมาแล้ว (มัธยฐาน) <b>${durTxt(du.h_med)}</b> · คาดลดใน (มัธยฐาน) <b>${durTxt(du.r_med)}</b>${bar(du.r_km2, du.dur_km2)}` +
      REM_LBL.map((t, k) => `<span style="white-space:nowrap;margin-right:8px"><span class="sw" style="background:${REM_COL[k]}"></span>${t} ${nf(du.r_km2[k], 1)}</span>`).join("") : "";
    $$("#distTable tbody tr").forEach(tr => tr.onclick = () => zoomAmphoe(tr.dataset.a));
  }
  function zoomAmphoe(name) {
    if (!S.distLayer) return;
    S.distLayer.eachLayer(l => { if (l.feature.properties.shapeName === name) map.fitBounds(l.getBounds(), { padding: [20, 20] }); });
  }

  // ---------------------------------------------------------------- stations
  function renderStations() {
    if (S.stLayer) { map.removeLayer(S.stLayer); layerCtl.removeLayer(S.stLayer); }
    if (S.rainLayer) { map.removeLayer(S.rainLayer); layerCtl.removeLayer(S.rainLayer); }
    const st = S.stations || { rain: [], level: [] };
    S.stLayer = L.layerGroup(st.level.map(s => {
      const lv = s.diff > 0 ? 5 : (s.situation || 3);
      const mk = L.circleMarker([s.lat, s.lon], { renderer, radius: 7, color: "#fff", weight: 2, fillColor: WL_COL[lv] || "#64748b", fillOpacity: 1 });
      mk.bindPopup(wlPopup(s)); s._mk = mk; return mk;
    }));
    if (S.view !== "simple") S.stLayer.addTo(map);
    layerCtl.addOverlay(S.stLayer, "สถานีระดับน้ำ (ThaiWater)");
    S.rainLayer = L.layerGroup(st.rain.filter(s => s.rain_24h > 0).map(s => {
      const mk = L.circleMarker([s.lat, s.lon], { renderer, radius: 3 + Math.min(s.rain_24h, 150) / 20, color: "#334155", weight: 1, fillColor: binCol(s.rain_24h, RAIN_BR, RAIN_COL), fillOpacity: .9 });
      mk.bindPopup(`<div class="pp"><h4>${s.name}</h4><div class="grid"><span>ฝน 24 ชม.</span><span><b>${nf(s.rain_24h, 1)}</b> มม.</span><span>ฝน 1 ชม.</span><span>${nf(s.rain_1h, 1)} มม.</span><span>เวลา</span><span>${s.time || "–"}</span><span>หน่วยงาน</span><span>${s.agency}</span><span>พื้นที่</span><span>${s.amphoe} ${s.province}</span></div></div>`);
      s._mk = mk; return mk;
    }));
    layerCtl.addOverlay(S.rainLayer, "สถานีวัดฝน 24 ชม. (ThaiWater)");
    const lv = [...st.level].sort((a, b) => b.diff - a.diff);
    $("#wlList").innerHTML = lv.length ? lv.map((s, k) => {
      const l = s.diff > 0 ? 5 : (s.situation || 3);
      return `<div class="it" data-k="${k}"><div class="t"><span>${s.code} ${s.name}</span><span class="badge" style="background:${WL_COL[l]}">${WL_LBL[l]}</span></div>
      <div class="m">ระดับ ${nf(s.wl, 2)} ม.รทก. · ตลิ่ง ${nf(s.bank, 2)} · ${s.diff > 0 ? `<b style="color:var(--bad)">ล้นตลิ่ง ${nf(s.diff, 2)} ม.</b>` : `ต่ำกว่าตลิ่ง ${nf(-s.diff, 2)} ม.`} ${s.trend == null ? "" : s.trend > 0 ? "▲" + nf(s.trend, 2) : s.trend < 0 ? "▼" + nf(-s.trend, 2) : "■"}</div></div>`;
    }).join("") : `<p class="note">ไม่มีข้อมูลระดับน้ำ</p>`;
    $$("#wlList .it").forEach(el => el.onclick = () => { const s = lv[+el.dataset.k]; map.setView([s.lat, s.lon], 12); s._mk.openPopup(); });
    const rs = [...st.rain].filter(s => s.province === "นครสวรรค์").sort((a, b) => b.rain_24h - a.rain_24h).slice(0, 12);
    $("#rainList").innerHTML = rs.map((s, k) => `<div class="it" data-k="${k}"><div class="t"><span>${s.name}</span><span>${nf(s.rain_24h, 1)} มม.</span></div><div class="m">${s.amphoe} · ${s.agency} · ${s.time}</div></div>`).join("") || `<p class="note">ไม่มีข้อมูล</p>`;
    $$("#rainList .it").forEach(el => el.onclick = () => { const s = rs[+el.dataset.k]; if (!map.hasLayer(S.rainLayer)) S.rainLayer.addTo(map); map.setView([s.lat, s.lon], 12); s._mk.openPopup(); });
  }
  function wlPopup(s) {
    const l = s.diff > 0 ? 5 : (s.situation || 3);
    return `<div class="pp"><h4>${s.code} ${s.name}</h4><div class="grid">
      <span>สถานการณ์</span><span><span class="badge" style="background:${WL_COL[l]}">${WL_LBL[l]}</span></span>
      <span>ระดับน้ำ</span><span><b>${nf(s.wl, 2)}</b> ม.รทก.</span><span>ตลิ่งต่ำสุด</span><span>${nf(s.bank, 2)} ม.รทก.</span>
      <span>เทียบตลิ่ง</span><span>${s.diff > 0 ? "+" : ""}${nf(s.diff, 2)} ม.</span><span>ความจุลำน้ำ</span><span>${nf(s.storage_pct, 0)}%</span>
      ${s.q != null ? `<span>ปริมาณน้ำไหล</span><span><b>${nf(s.q)}</b>${s.qmax ? " / " + nf(s.qmax) : ""} ลบ.ม./วิ</span>` : ""}
      <span>แนวโน้ม</span><span>${s.trend == null ? "–" : (s.trend > 0 ? "ขึ้น " : s.trend < 0 ? "ลง " : "ทรงตัว ") + nf(Math.abs(s.trend), 2) + " ม."}</span>
      <span>เวลา</span><span>${s.time}</span><span>หน่วยงาน</span><span>${s.agency}</span></div></div>`;
  }

  // ---------------------------------------------------------------- hex popup
  function hexPopup(i, latlng) {
    const st = S.status, p = S.params;
    const c = st.c[i], a = p.amphoe_list[p.amph[i]];
    const hrsTxt = st.h[i] < 0 ? "ไม่ทราบ (ไม่มีประวัติสถานี)" : st.h[i] > 0 && (st.s[i] & 2) && !(st.s[i] & 1) ? durTxt(st.h[i]) + " (นับจากสถานีล้นตลิ่ง)" : st.h[i] > 0 ? durTxt(st.h[i]) : "–";
    const fr = +S.frames.c[S.frame][i];
    const html = `<div class="pp"><h4>อ.${a} · hex #${i}</h4>
      <div class="grid">
        <span>สถานะตอนนี้</span><span><span class="sw" style="background:${CLS_COL[c] === CLS_COL[0] ? "#fff" : CLS_COL[c]}"></span><b>${CLS_SHORT[c]}</b></span>
        <span>ความลึกเฉลี่ย (ส่วนที่ท่วม)</span><span>${nf(st.d[i])} ซม.</span>
        ${st.f ? `<span>พื้นที่ท่วมใน hex</span><span>${nf(st.f[i])}%</span>` : ""}
        ${st.wse ? `<span>ระดับผิวน้ำ</span><span>${nf(st.wse[i], 2)} ม.</span>` : ""}
        <span>สาเหตุ</span><span>${SRC_LBL[st.s[i]]}</span>
        <span>ท่วมมาแล้ว</span><span>${hrsTxt}</span>
        <span>คาดว่าจะลดลงใน</span><span>${durTxt(st.r[i])}</span>
        ${S.frames.t[S.frame] !== S.frames.now ? `<span>ณ เวลาที่เลือก</span><span>${CLS_SHORT[fr]}</span>` : ""}
      </div><hr><div class="grid">
        <span>ฝน 24 ชม. / 72 ชม.</span><span>${nf(st.p24[i])} / ${nf(st.p72[i])} มม.</span>
        <span>ฝน 7 วัน</span><span>${nf(st.p7d[i])} มม.</span>
        <span>พยากรณ์ 24 / 72 ชม.</span><span>${nf(st.f24[i])} / ${nf(st.f72[i])} มม.</span>
        <span>สูงสุดใน 72 ชม.</span><span>${CLS_SHORT[st.m72[i]]} (${nf(st.dm72[i])} ซม.)</span>
        ${st.u72 && st.u72[i] > 0 ? `<span>น้ำล้นตลิ่ง/ต้นน้ำ 72 ชม.</span><span>${nf(st.u72[i])} ซม.</span>` : ""}
        ${st.ui && st.ui[i] > 0 ? `<span>น้ำไหลเข้าจากนอกจังหวัด 72 ชม.</span><span>${nf(st.ui[i])} มม.</span>` : ""}
      </div><hr><div class="grid">
        <span>HAND เฉลี่ย / P10</span><span>${nf(p.hand[i], 1)} / ${nf(p.hand_p10[i], 1)} ม.</span>
        <span>สัดส่วนที่ลุ่ม (HAND&lt;2ม.)</span><span>${nf(p.f_low[i] * 100)}%</span>
        <span>Curve Number</span><span>${nf(p.cn[i])}</span>
        <span>ความลาดชัน</span><span>${nf(p.slope[i], 1)}%</span>
        <span>นา / เมือง</span><span>${nf(p.f_crop[i] * 100)}% / ${nf(p.f_built[i] * 100)}%</span>
      </div>${S.phHex ? `<hr><div class="grid">
        <span>ท่วม ≥ 30 วัน (S1 2560–68)</span><span>${S.phHex.yrs[i]} / ${S.phHex.n_years} ปี${S.phHex.last[i] ? ` · ล่าสุด ${S.phHex.last[i] + 543}` : ""}</span>
        <span>พื้นที่ท่วม ≥ 30 วัน ≥ 3 ปี</span><span>${nf(S.phHex.rep_pct[i])}% ของ hex</span>
        <span>ตำบล</span><span>${S.ph?.tambon?.[S.phHex.tam[i]]?.name || "–"}</span></div>` : ""}</div>`;
    L.popup({ maxWidth: 320 }).setLatLng(latlng).setContent(html).openOn(map);
  }


  // ---------------------------------------------------------------- upstream (4 provinces)
  async function loadUpstreamStatic() {
    if (S.upStatic) return;
    const [basins, ents] = await Promise.all([jOpt("data/static/upstream_basins.geojson", "s2"), jOpt("data/static/upstream_entries.json", "s2")]);
    S.upStatic = { basins, ents };
    if (basins) {
      S.basinLayer = L.geoJSON(basins, { renderer, style: f => ({ color: PROV_COL[f.properties.pcode] || "#64748b", weight: 1, fillColor: PROV_COL[f.properties.pcode] || "#64748b", fillOpacity: .12 }),
        onEachFeature: (f, l) => l.bindPopup(`<b>${f.properties.pname}</b><br>พื้นที่ที่ไหลลงนครสวรรค์ ${nf(f.properties.contrib_km2)} กม²`) });
      layerCtl.addOverlay(S.basinLayer, "ลุ่มน้ำต้นน้ำ 4 จังหวัด (ไหลเข้า นว.)");
    }
    if (ents) {
      const big = ents.entries.filter(e => e.area_km2 >= 50);
      S.entryLayer = L.layerGroup(big.map(e => L.circleMarker([e.lat, e.lon], { renderer, radius: e.major ? 9 : 4 + Math.min(e.area_km2, 1000) / 250, color: "#fff", weight: 2, fillColor: e.major ? "#0f172a" : "#475569", fillOpacity: .9 })
        .bindPopup(`<b>จุดน้ำเข้า${e.river ? " " + e.river : ""}</b><br>อ.${e.amphoe}<br>พื้นที่รับน้ำจาก 4 จังหวัด ${nf(e.area_km2)} กม²<br>` +
          Object.entries(e.by_prov).map(([c, a]) => `${ents.province_names[c]} ${nf(a)} กม²`).join("<br>"))));
      layerCtl.addOverlay(S.entryLayer, "จุดน้ำเข้าจากต้นน้ำ");
    }
  }
  async function showUpstreamMap() {
    await loadUpstreamStatic();
    if (S.basinLayer && !map.hasLayer(S.basinLayer)) { S.basinLayer.addTo(map); map.fitBounds(S.basinLayer.getBounds().extend(S.provLayer ? S.provLayer.getBounds() : S.basinLayer.getBounds()), { padding: [10, 10] }); }
    if (S.entryLayer && !map.hasLayer(S.entryLayer)) S.entryLayer.addTo(map);
    if (S.upGaugeLayer && !map.hasLayer(S.upGaugeLayer)) S.upGaugeLayer.addTo(map);
  }
  function hydroSvg(r) {
    const W = 340, H = 92, pl = 4, pb = 12;
    const hist = (r.hist || []).filter(x => x[1] != null && x[0] >= r.t[0]);
    const vals = [...r.q_fc.filter(v => v != null), ...hist.map(x => x[1]), r.qmax || 0, ...(r.q_obs != null ? [r.q_obs] : [])];
    const t0 = r.t[0], t1 = r.t[r.t.length - 1];
    const mx = Math.max(1, ...vals) * 1.1;
    const X = t => pl + (t - t0) / (t1 - t0) * (W - pl * 2), Y = v => H - pb - v / mx * (H - pb - 4);
    const path = pts => pts.length ? "M" + pts.map(([t, v]) => `${X(t).toFixed(1)},${Y(v).toFixed(1)}`).join("L") : "";
    const fc = r.t.map((t, k) => [t, r.q_fc[k]]).filter(x => x[1] != null);
    const now = S.upstream.series.now, iN = r.t.indexOf(now);
    // ย้อนหลังโดยประมาณ = ค่าตรวจวัดตอนนี้ + การเปลี่ยนแปลงของน้ำท่าจากแบบจำลอง
    const est = r.q_obs != null && iN > 0 && hist.length < 6 ? r.t.slice(0, iN + 1).map((t, k) => [t, Math.max(r.q_obs + (r.q_model[k] - r.q_model[iN]), 0)]) : [];
    vals.push(...est.map(x => x[1]));
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="color:var(--ink)">
      ${r.qmax ? `<line x1="0" x2="${W}" y1="${Y(r.qmax)}" y2="${Y(r.qmax)}" stroke="#dc2626" stroke-dasharray="4 3" opacity=".8"/><text x="${W - 2}" y="${Y(r.qmax) - 3}" font-size="9" text-anchor="end" fill="#dc2626">ความจุ ${nf(r.qmax)}</text>` : ""}
      <line x1="${X(now)}" x2="${X(now)}" y1="4" y2="${H - pb}" stroke="currentColor" opacity=".35" stroke-dasharray="2 2"/>
      <path d="${path(est)}" fill="none" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="3 2"/>
      <path d="${path(hist.map(x => [x[0], x[1]]))}" fill="none" stroke="#0f172a" stroke-width="1.8"/>
      <path d="${path(fc)}" fill="none" stroke="#7c3aed" stroke-width="2"/>
      ${r.q_obs != null ? `<circle cx="${X(now)}" cy="${Y(r.q_obs)}" r="3" fill="#0f172a"/>` : ""}
      <text x="${X(now) + 3}" y="${H - 2}" font-size="9" fill="currentColor" opacity=".6">ตอนนี้</text>
      <text x="2" y="10" font-size="9" fill="currentColor" opacity=".6">${nf(mx / 1.1)} ลบ.ม./วิ</text></svg>`;
  }
  function recTxt(r) {
    const rc = r.recession; if (!rc) return "";
    const ob = r.overbank_now && r.overbank_since ? Math.round((S.upstream.series.now - r.overbank_since) / 3600) : null;
    return `น้ำลด k = ${nf(rc.k_h)} ชม.${rc.k_from_hist ? " (จากช่วงน้ำลดจริง 30 วัน)" : " (ค่าเริ่มต้น)"} · แนวโน้ม ${rc.trend_qph > 0.5 ? "▲ ขึ้น" : rc.trend_qph < -0.5 ? "▼ ลง" : "ทรงตัว"} ${nf(Math.abs(rc.trend_qph), 1)} ลบ.ม./วิ/ชม.` +
      (ob != null ? ` · <b style="color:var(--bad)">ล้นตลิ่งมาแล้ว ${durTxt(ob)}</b>` : "") +
      (r.h_below_bank > 0 ? ` · คาดลดต่ำกว่าความจุลำน้ำใน <b>${durTxt(r.h_below_bank)}</b>` : "") + "<br>";
  }
  function renderUpstream() {
    const u = S.upstream;
    if (!u) { $("#upRivers").innerHTML = `<p class="note">ยังไม่มีข้อมูลต้นน้ำ — รัน ArcGIS Pro tool "Build Upstream Basins" ก่อน</p>`; return; }
    const k = (v, un, l, hot) => `<div class="kpi${hot ? " hot" : ""}"><div class="v">${v}<small>${un}</small></div><div class="l">${l}</div></div>`;
    const over = u.rivers.filter(r => r.q_obs != null && r.qmax && r.q_obs > r.qmax).length;
    const fpMax = u.rivers.reduce((a, r) => a + (r.floodplain?.area_max || 0), 0);
    $("#upKpi").innerHTML = k(nf(u.total.q_now), "ลบ.ม./วิ", "น้ำท่าจาก 4 จังหวัดไหลเข้าตอนนี้ (แบบจำลอง)") + k(nf(u.total.vol_in_72h_mcm, 1), "ล้าน ลบ.ม.", "จะไหลเข้าใน 72 ชม.") +
      k(nf(over), "สาย", "ลำน้ำเกินความจุตอนนี้", over > 0) + k(nf(fpMax), "กม²", "ที่ราบลุ่มน้ำล้นตลิ่ง สูงสุด 72 ชม.", fpMax > 0);
    $("#upRivers").innerHTML = u.rivers.map(r => {
      const pct = r.q_obs != null && r.qmax ? r.q_obs / r.qmax * 100 : null;
      const col = pct == null ? "#94a3b8" : pct >= 100 ? "#dc2626" : pct >= 80 ? "#f59e0b" : "#16a34a";
      const fp = r.floodplain || {};
      return `<div class="rv"><div class="t"><span>${r.name}</span><span style="color:${col}">${pct == null ? "–" : nf(pct) + "% ความจุ"}</span></div>
        <div class="bar"><i style="width:${Math.min(pct || 0, 100)}%;background:${col}"></i></div>
        <div class="m">สถานี ${r.gauge} ${r.gauge_name} · ตอนนี้ <b>${nf(r.q_obs)}</b>${r.q_is_est ? "*" : ""} / ${nf(r.qmax)} ลบ.ม./วิ${r.q_is_est ? " (*ประมาณจากระดับน้ำ)" : ""} · ${r.diff > 0 ? `<b style="color:var(--bad)">ล้นตลิ่ง ${nf(r.diff, 2)} ม.</b>` : `ต่ำกว่าตลิ่ง ${nf(-(r.diff ?? NaN), 2)} ม.`}<br>
        ${recTxt(r)}
        คาดสูงสุด 72 ชม. <b>${nf(r.peak_q)}</b> ลบ.ม./วิ (${r.peak_t ? fmtT(r.peak_t) : "–"})${fp.area_max ? ` · น้ำล้นตลิ่งที่ราบลุ่ม ${nf(fp.area_now)} → สูงสุด ${nf(fp.area_max)} กม² (${nf(fp.vol_max, 1)} ล้าน ลบ.ม.)` : ""}</div>
        ${hydroSvg(r)}</div>`;
    }).join("") + `<div class="note"><span class="sw" style="background:#0f172a"></span>ค่าตรวจวัดรายชั่วโมง (ThaiWater; ช่วงที่ไม่มีปริมาณน้ำเติมด้วย rating curve จากระดับน้ำ) <span class="sw" style="background:#7c3aed;margin-left:8px"></span>พยากรณ์ = ค่าตรวจวัดตอนนี้ → ขึ้นต่อตามแนวโน้มแบบหน่วง แล้วลดตามอัตราน้ำลดในอดีต (k) + น้ำท่าจากฝนใหม่ใน 4 จังหวัด · แม่น้ำเจ้าพระยา = ปิง + น่าน ล่าช้า 12 ชม.</div>`;
    $("#upProv tbody").innerHTML = u.provinces.map(p => `<tr><td><span class="sw" style="background:${PROV_COL[p.code]}"></span>${p.name}</td><td>${nf(p.contrib_km2)}</td><td>${nf(p.rain7d)}</td><td>${nf(p.fc72)}</td><td><b>${nf(p.vol_in_72h_mcm, 1)}</b></td></tr>`).join("");
    // stacked arrival chart
    const s = u.series, W = 340, H = 120, n = s.t.length, codes = Object.keys(s.q);
    const tot = s.t.map((_, i) => codes.reduce((a, c) => a + s.q[c][i], 0)); const mx = Math.max(1, ...tot);
    const X = i => i / (n - 1) * W, Y = v => H - 14 - v / mx * (H - 24);
    let base = new Array(n).fill(0), areas = "";
    codes.forEach(c => { const top = base.map((b, i) => b + s.q[c][i]);
      areas += `<path d="M${top.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("L")}L${base.map((v, i) => [X(i), Y(v)]).reverse().map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join("L")}Z" fill="${PROV_COL[c]}" opacity=".75"/>`; base = top; });
    const iNow = s.t.indexOf(s.now);
    $("#upChart").innerHTML = `<h3>อัตราน้ำท่าที่ไหลเข้า นว. แยกจังหวัด (ลบ.ม./วิ)</h3><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="color:var(--ink)">${areas}
      <line x1="${X(iNow)}" x2="${X(iNow)}" y1="6" y2="${H - 14}" stroke="#dc2626" stroke-dasharray="3 2"/><text x="${X(iNow) + 3}" y="14" font-size="9" fill="#dc2626">ตอนนี้</text>
      <text x="0" y="10" font-size="9" fill="currentColor" opacity=".6">${nf(mx)}</text><text x="0" y="${H - 2}" font-size="9" fill="currentColor" opacity=".6">${fmtT(s.t[0])}</text><text x="${W}" y="${H - 2}" font-size="9" text-anchor="end" fill="currentColor" opacity=".6">${fmtT(s.t[n - 1])}</text></svg>`;
    // upstream gauges
    const g = [...(u.gauges || [])].sort((a, b) => (b.q || 0) - (a.q || 0));
    $("#upGauges").innerHTML = g.map((x, i) => { const pct = x.q != null && x.qmax ? x.q / x.qmax * 100 : x.storage_pct;
      return `<div class="it" data-k="${i}"><div class="t"><span>${x.code} ${x.name}</span><span>${x.q != null ? nf(x.q) + " ลบ.ม./วิ" : nf(x.storage_pct) + "% ลำน้ำ"}</span></div>
      <div class="m">${x.river || ""} · ${x.province} · ${pct != null ? nf(pct) + "% ความจุ" : ""} · ${x.diff > 0 ? `<b style="color:var(--bad)">ล้นตลิ่ง ${nf(x.diff, 2)} ม.</b>` : `ต่ำกว่าตลิ่ง ${nf(-x.diff, 2)} ม.`} ${x.trend > 0 ? "▲" : x.trend < 0 ? "▼" : ""}</div></div>`; }).join("");
    if (S.upGaugeLayer) { map.removeLayer(S.upGaugeLayer); layerCtl.removeLayer(S.upGaugeLayer); }
    S.upGaugeLayer = L.layerGroup(g.map(x => { const lv = x.diff > 0 ? 5 : (x.situation || 3);
      x._mk = L.circleMarker([x.lat, x.lon], { renderer, radius: 7, color: "#0f172a", weight: 2, fillColor: WL_COL[lv], fillOpacity: 1 }).bindPopup(wlPopup(x)); return x._mk; }));
    layerCtl.addOverlay(S.upGaugeLayer, "สถานีวัดน้ำต้นน้ำ (RID)");
    $$("#upGauges .it").forEach(el => el.onclick = () => { const x = g[+el.dataset.k]; if (!map.hasLayer(S.upGaugeLayer)) S.upGaugeLayer.addTo(map); map.setView([x.lat, x.lon], 11); x._mk.openPopup(); });
    const cg = u.calib?.group || {};
    $("#upNote").textContent = (Object.keys(cg).length ? `ปรับขนาดน้ำท่าแบบจำลองด้วยปริมาณน้ำจริง: ${Object.entries(cg).map(([k2, v2]) => `${k2 === "ping" ? "กลุ่มปิง (กำแพงเพชร)" : "กลุ่มน่าน-ยม (พิษณุโลก พิจิตร เพชรบูรณ์)"} ×${nf(v2, 2)}`).join(", ")} · ` : "") + "พื้นที่ลุ่มน้ำและเวลาเดินทางของน้ำวิเคราะห์ด้วย ArcGIS Pro (Copernicus DEM 90 ม.) · น้ำท่าคำนวณด้วย SCS-CN จากฝน Open-Meteo ที่ปรับแก้ด้วยสถานี · ความจุลำน้ำ/ปริมาณน้ำจริงจากกรมชลประทานผ่าน ThaiWater";
  }


  // ---------------------------------------------------------------- 2D hotspots + drainage assets
  S.hotSnap = "now"; S.hotLayers = {};
  async function loadDrainage() {
    if (S.drainLoaded) return; S.drainLoaded = true;
    const [lines, assets] = await Promise.all([jOpt("data/static/drainage.geojson", "s3"), jOpt("data/static/drainage_assets.json", "s3")]);
    S.drainAssets = assets;
    if (lines) {
      S.drainLines = L.geoJSON(lines, { renderer, interactive: false, style: f => f.properties.k === "dyke" ? { color: "#92400e", weight: 2.2, opacity: .9 } : { color: "#0891b2", weight: f.properties.k === "canal" ? 1.6 : 1, opacity: .7 } });
      layerCtl.addOverlay(S.drainLines, "คลอง/คูระบาย/คันกั้นน้ำ (OSM)");
    }
    if (assets) {
      const pm = assets.pumps.map(x => L.circleMarker([x.lat, x.lon], { renderer, radius: 5, color: "#fff", weight: 1.5, fillColor: "#0e7490", fillOpacity: 1 })
        .bindPopup(`<b>${x.name}</b><br>สถานีสูบน้ำ · ${nf(x.cap_m3s, 1)} ลบ.ม./วิ${x.cap_default ? " (ค่าตั้งต้น — แก้ได้ใน drainage_assets_user.csv)" : ""}<br><span class="note">${x.src}</span>`));
      const gt = assets.gates.map(x => L.marker([x.lat, x.lon], { icon: L.divIcon({ className: "", html: `<div style="width:10px;height:10px;background:#92400e;border:2px solid #fff;transform:rotate(45deg)"></div>`, iconSize: [10, 10] }) })
        .bindPopup(`<b>${x.name}</b><br>${{ sluice_gate: "ประตูระบายน้ำ", lock_gate: "ประตูเรือสัญจร", weir: "ฝาย", dam: "เขื่อน/ฝาย" }[x.kind] || x.kind}<br><span class="note">${x.src}</span>`));
      S.assetLayer = L.layerGroup([...pm, ...gt]);
      layerCtl.addOverlay(S.assetLayer, "สถานีสูบน้ำ / ประตูระบายน้ำ");
    }
    renderHot();
  }
  function showHotMap() {
    loadDrainage().then(() => { [S.drainLines, S.assetLayer].forEach(l => l && !map.hasLayer(l) && l.addTo(map)); });
    Object.values(S.hotLayers).forEach(l => !map.hasLayer(l) && l.addTo(map));
    Object.values(S.aoiLayers || {}).forEach(l => l && l !== "loading" && !map.hasLayer(l) && l.addTo(map));
  }
  function renderHot() {
    const h = S.hot || {}, ids = Object.keys(h).filter(k => !k.startsWith("_"));
    Object.values(S.hotLayers).forEach(l => { map.removeLayer(l); layerCtl.removeLayer(l); }); S.hotLayers = {};
    ids.forEach(k => { const x = h[k], fn = x.png[S.hotSnap] || (S.hotSnap === "rem" ? null : x.png.now); if (!fn) return;
      S.hotLayers[k] = L.imageOverlay(`data/live/hotspots/${fn}?v=${encodeURIComponent(h._generated || "")}`, x.bounds, { opacity: .85, interactive: false });
      layerCtl.addOverlay(S.hotLayers[k], `2D: ${x.name}`); });
    if ($('.tabs button[data-tab="hot"]').classList.contains("active")) showHotMap();
    $("#hotList").innerHTML = ids.length ? ids.map(k => { const x = h[k];
      const s = x.series || [], mx = Math.max(1, ...s.map(r => r[1])), W = 340, H = 60, t0 = s.length ? s[0][0] : 0, t1 = s.length ? s[s.length - 1][0] : 1;
      const path = s.map((r, i) => `${i ? "L" : "M"}${((r[0] - t0) / (t1 - t0 || 1) * W).toFixed(1)},${(H - 4 - r[1] / mx * (H - 10)).toFixed(1)}`).join("");
      const xn = (x.now - t0) / (t1 - t0 || 1) * W;
      return `<div class="rv" data-k="${k}" style="cursor:pointer"><div class="t"><span>${x.name}</span><span>${nf(x.area_now_km2, 1)} → ${nf(x.area_max72_km2, 1)} กม²</span></div>
        <div class="m">พื้นที่ท่วม ≥10 ซม. ตอนนี้ → สูงสุด 72 ชม. · สถานีขอบเขต ${x.stations.join(", ")} · ${nf(x.runtime_s)} วินาที</div>
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:60px"><path d="${path}" fill="none" stroke="#2563eb" stroke-width="2"/>
        <line x1="${xn}" x2="${xn}" y1="0" y2="${H}" stroke="#dc2626" stroke-dasharray="3 2"/><text x="2" y="10" font-size="9" fill="currentColor" opacity=".6">${nf(mx, 1)} กม²</text></svg></div>`; }).join("")
      : `<p class="note">ยังไม่มีผล 2D — รัน <code>python pipeline/run_hotspots.py --site .</code> หรือรอรอบถัดไปของ GitHub Actions (ทุก 6 ชม.)</p>`;
    $$("#hotList .rv").forEach(el => el.onclick = () => { const x = h[el.dataset.k]; showHotMap(); map.fitBounds(x.bounds); });
    renderPond(h, ids);
    const a = S.drainAssets, sm = S.meta?.summary || {};
    $("#drainInfo").innerHTML = a ? `สถานีสูบน้ำ ${a.pumps.length} แห่ง (ความจุรวม ${nf(a.pumps.reduce((q, x) => q + x.cap_m3s, 0))} ลบ.ม./วิ) · ประตูระบายน้ำ/ฝาย ${a.gates.length} แห่ง ·
      คลอง/คูระบาย ${nf((S.params.canal_km || []).reduce((q, x) => q + x, 0))} กม. ${sm.pumped_72h_mcm != null ? `· คาดสูบออก 72 ชม. ${nf(sm.pumped_72h_mcm, 2)} ล้าน ลบ.ม.` : ""}<br>
      ข้อมูลจาก OpenStreetMap — ความจุสถานีสูบที่ไม่ทราบใช้ค่าตั้งต้น 3 ลบ.ม./วิ แก้/เพิ่มได้ในไฟล์ <code>data/static/drainage_assets_user.csv</code>` : "เปิดแท็บนี้เพื่อโหลดข้อมูล";
    const c = S.meta?.calibration;
    $("#calibInfo").innerHTML = c ? `เวลาระบาย ×${nf(c.t_mult, 2)} · การล้นข้ามจุดล้น ×${nf(c.weir_c, 2)} · ความจุแอ่ง ×${nf(c.dcap_mult, 2)} ·
      คะแนน ${nf(c.score, 3)} (ค่าเริ่มต้น ${nf(c.baseline_score, 3)}) · สอบเทียบกับแบบจำลอง 2D ${c.hotspots?.length || 0} จุด เมื่อ ${c.calibrated_at ? new Date(c.calibrated_at).toLocaleString("th-TH", { timeZone: "Asia/Bangkok", dateStyle: "medium", timeStyle: "short" }) : "–"}`
      : "ยังไม่ได้สอบเทียบ — ใช้ค่าเริ่มต้นตามหลักอุทกวิทยา";
  }
  // ---------------------------------------------------------------- ระยะเวลาท่วมขังรายตำบล (โดเมนที่มี AOI เช่น แอ่งท่าตะโก)
  S.aoiLayers = {};
  function renderPond(h, ids) {
    const box = $("#pondBox"); if (!box) return;
    const withAoi = ids.filter(k => h[k].aoi);
    const P = withAoi.map(k => [k, h[k].ponding]).filter(([, p]) => p && p.tambon && p.tambon[0] && "patch_km2" in p.tambon[0]);
    const remTxt = (v, any) => !any ? "–" : v == null ? "> 14 วัน" : v === 0 ? "ลดแล้ว" : durTxt(v);
    const remBar = r => { const tot = r.rem_km2.reduce((a, b) => a + b, 0); return tot > 0 ? `<div style="display:flex;height:7px;border-radius:3px;overflow:hidden;background:var(--line);margin-top:2px">${r.rem_km2.map((v, i) => `<span style="width:${v / tot * 100}%;background:${REM_COL[i]}"></span>`).join("")}</div>` : ""; };
    const leg = `<div class="note" style="margin:4px 0">${REM_COL.map((c, i) => `<span class="sw" style="background:${c}"></span>${REM_LBL[i]}`).join(" ")}</div>`;
    box.innerHTML = s1Html() + evHtml() + scnHtml() + P.map(([k, p]) => { const t = p.total_new || {};
      const tr = p.tambon.map(r => `<tr${r.in_existing ? ' style="opacity:.65"' : ""}><td>${r.name}<div class="note">${r.district}${r.in_existing ? " · มีในโดเมนชุมแสงแล้ว" : ""}</div></td>
          <td>${nf(r.wet_now_km2, 1)}</td><td>${nf(r.patch_km2, 1)}</td><td>${nf(r.depth_p95_m, 2)}</td><td>${nf(r.wet_ge7d_km2, 1)}</td>
          <td>${remTxt(r.rem_med_h, r.patch_km2 > 0)} / ${remTxt(r.rem_p90_h, r.patch_km2 > 0)}${remBar(r)}</td><td>${r.hex_rem_med_h == null ? "–" : durTxt(r.hex_rem_med_h)}</td></tr>`).join("");
      return `<h3>สถานการณ์ปัจจุบัน: ระยะเวลาท่วมขังรายตำบล — ${h[k].name}</h3>${leg}
        <p class="note">จำลองต่อหลังพยากรณ์ 72 ชม. อีก ${Math.round((p.sim_h_after_now - p.fc_h) / 24)} วัน <b>โดยสมมติว่าไม่มีฝนเพิ่ม</b> และระดับน้ำแม่น้ำลดตามอัตราน้ำลดของสถานี ·
          นับเฉพาะผืนน้ำ ≥ ${nf(p.min_patch_km2, 2)} กม² ไม่รวมแหล่งน้ำถาวร · รันเมื่อ ${new Date(p.t_run * 1000).toLocaleString("th-TH", { timeZone: "Asia/Bangkok", dateStyle: "medium", timeStyle: "short" })} ·
          ${p.tambon.filter(r => !r.in_existing).length} ตำบลที่เพิ่มใหม่: ท่วมตอนนี้ <b>${nf(t.wet_now_km2, 1)}</b> กม² · ขัง ≥ 7 วัน <b>${nf(t.wet_ge7d_km2, 1)}</b> กม² ·
          คาดลด (มัธยฐาน/P90) <b>${remTxt(t.rem_med_h, t.patch_km2 > 0)} / ${remTxt(t.rem_p90_h, t.patch_km2 > 0)}</b></p>
        <div style="overflow-x:auto"><table class="tbl"><thead><tr><th>ตำบล</th><th>ท่วมตอนนี้ กม²</th><th>ผืนท่วม กม²</th><th>ลึก P95 ม.</th><th>ขัง ≥7 วัน กม²</th><th>คาดลด มัธยฐาน/P90</th><th>โมเดล hex คาดลด</th></tr></thead>
        <tbody>${tr}</tbody></table></div>`; }).join("");
    $$("#pondBox [data-scn]").forEach(b => b.onclick = () => { const [key, lay] = b.dataset.scn.split(":"); tkShow(`scn:${key}:${lay}`); });
    $$("#pondBox [data-s1]").forEach(b => b.onclick = () => tkShow("s1"));
    $$("#pondBox [data-ev]").forEach(b => b.onclick = () => tkShow(`ev:${b.dataset.ev}`));
  }
  // ---------------------------------------------------------------- แท็บท่าตะโก: ขอบเขตตำบล + ชั้นผลวิเคราะห์ (ไม่ขึ้นกับผล 2D รายชั่วโมง)
  S.tkLayer = null; S.tkAoi = null; S.tkSel = "s1";
  function tkRefresh() { const h = S.hot || {}; renderPond(h, Object.keys(h).filter(k => !k.startsWith("_"))); tkLegend(); }
  jOpt("data/static/hotspots/thatako_aoi.geojson", "s4").then(g => { if (!g) return;
    S.tkAoi = L.geoJSON(g, { renderer, style: f => ({ color: f.properties.in_existing ? "#64748b" : "#7c3aed", weight: 1.6, fill: false, dashArray: f.properties.in_existing ? "4 3" : null }),
      onEachFeature: (f, l) => l.bindTooltip(`ต.${f.properties.name} (อ.${f.properties.district})`, { sticky: true }) });
    layerCtl.addOverlay(S.tkAoi, "ท่าตะโก: ขอบเขตตำบลที่ศึกษา");
    if ($('.tabs button[data-tab="thatako"]').classList.contains("active")) S.tkAoi.addTo(map); });
  const TK_LEG = {
    s1: [["#bfdbfe", "1–2 ปี"], ["#60a5fa", "3–4"], ["#2563eb", "5–6"], ["#1e3a8a", "7–9 ปี"], ["#94a3b8", "แหล่งน้ำถาวร"]],
    ev: [["#2563eb", "ท่วม ≥ 30 วัน ตรงกัน"], ["#ea580c", "จริงแต่จำลองไม่ถึง"], ["#facc15", "จำลองเกิน"], ["#94a3b8", "แหล่งน้ำถาวร"]],
    scn: [["#fde047", "ลดใน 1–3 วัน"], ["#fb923c", "3–7 วัน"], ["#dc2626", "7–14 วัน"], ["#7f1d1d", "> 14 วัน"]] };
  const TK_TTL = { s1: "จำนวนปีที่ท่วมต่อเนื่อง ≥ 30 วัน (Sentinel-1 ปี 2560–2568)", ev: y => `ปี ${+y + 543}: ท่วม ≥ 30 วัน แบบจำลอง vs ภาพเรดาร์วันเดียวกัน`,
    scn: "สถานการณ์ E (ฝน 250 มม./5 วัน + น้ำล้นตลิ่ง): เวลาน้ำลดนับจากฝนเริ่มตก" };
  function tkLegend() {
    const [t, a] = S.tkSel.split(":"), el = $("#tkLegend"); if (!el) return;
    el.innerHTML = t === "off" ? "" : `<b>${t === "ev" ? TK_TTL.ev(a) : TK_TTL[t]}</b><br>${TK_LEG[t].map(([c, l]) => `<span class="sw" style="background:${c}"></span>${l}`).join(" ")}`;
    $$("#tkSeg button").forEach(b => b.classList.toggle("active", b.dataset.tk === S.tkSel || (t === "scn" && b.dataset.tk.startsWith("scn") && b.dataset.tk === S.tkSel)));
  }
  function tkShow(sel, fit = true) {
    S.tkSel = sel; const [t, a, b] = sel.split(":");
    if (S.tkLayer) { map.removeLayer(S.tkLayer); layerCtl.removeLayer(S.tkLayer); S.tkLayer = null; }
    let url, bounds, label;
    if (t === "s1" && S.s1h) { url = `data/static/hotspots/${S.s1h.png}`; bounds = S.s1h.bounds; label = "ท่าตะโก: น้ำท่วมซ้ำ Sentinel-1 2560–2568"; }
    if (t === "ev" && S.ev?.years?.[a]?.png) { url = `data/static/hotspots/ev/${S.ev.years[a].png}`; bounds = S.ev._bounds; label = `ท่าตะโก: เหตุการณ์ปี ${+a + 543} จำลอง vs ดาวเทียม`; }
    if (t === "scn" && S.scn?.[a]) { url = `data/static/hotspots/scn/${S.scn[a].png[b || "rem"]}`; bounds = S.scn._bounds; label = `ท่าตะโก: สถานการณ์ ${a} (${b === "max" ? "ความลึกสูงสุด" : "เวลาน้ำลด"})`; }
    if (url) { S.tkLayer = L.imageOverlay(url, bounds, { opacity: .85, interactive: false }).addTo(map); layerCtl.addOverlay(S.tkLayer, label); if (fit) map.fitBounds(bounds); }
    if (S.tkAoi && !map.hasLayer(S.tkAoi)) S.tkAoi.addTo(map);
    const tab = $('.tabs button[data-tab="thatako"]'); if (!tab.classList.contains("active")) tab.click();
    tkLegend();
  }
  $$("#tkSeg button").forEach(b => b.onclick = () => tkShow(b.dataset.tk));
  $("#toThatako").onclick = e => { e.preventDefault(); $('.tabs button[data-tab="thatako"]').click(); };
  function showThatako() {
    if (S.tkAoi && !map.hasLayer(S.tkAoi)) S.tkAoi.addTo(map);
    if (!S.tkLayer && S.tkSel !== "off") tkShow(S.tkSel, false);
    map.fitBounds([[15.38, 100.12], [15.99, 100.70]]);
  }

  // ---------------------------------------------------------------- เคยท่วมนานแค่ไหน — Sentinel-1 ทั้งจังหวัด 2560–2568 (pipeline/s1_province.py)
  S.ph = null; S.phHex = null; S.phLayer = null; S.phTam = null;
  const beY = y => +y + 543;
  const HIST_WORD = y => y >= 6 ? "เกือบทุกปี" : y >= 3 ? "บ่อย" : y >= 1 ? "บางปี" : "";
  const HIST_W = y => y >= 6 ? 3 : y >= 3 ? 2 : y >= 1 ? 1 : 0;
  Promise.all([jOpt("data/static/s1_province.json", "s8"), jOpt("data/static/s1_province_hex.json", "s8")]).then(([a, b]) => {
    S.ph = a; S.phHex = b && a && b.n === (S.params?.n ?? b.n) ? b : null;
    renderHist(); if (S.meta && S.status) renderSimple(); if (S.mode === "hist") { histOverlay(true); renderLegend(); }
  });
  let tamP = null;
  function loadTam() {
    if (!tamP) tamP = jOpt("data/static/tambon_web.geojson", "s8").then(g => {
      if (!g) return null;
      S.phTam = L.geoJSON(g, { renderer, style: () => ({ color: "#7c2d12", weight: .8, opacity: .55, fill: true, fillOpacity: 0 }),
        onEachFeature: (f, l) => {
          l.bindTooltip(() => tamTip(f.properties.k), { sticky: true, direction: "top" });
          l.on("click", e => { if (!S.params) return; const i = nearestHex(e.latlng.lat, e.latlng.lng); if (i < 0) return;
            S.view === "simple" ? simplePopup(i, e.latlng) : hexPopup(i, e.latlng); });
        } });
      return S.phTam;
    });
    return tamP;
  }
  function tamTip(k) {
    const t = S.ph?.tambon?.[k]; if (!t) return "";
    const ys = Object.keys(t.ge30d_km2_by_year), yw = ys.reduce((a, y) => t.ge30d_km2_by_year[y] > t.ge30d_km2_by_year[a] ? y : a, ys[0]);
    const v = t.ge30d_ge3y_km2, w = t.ge30d_km2_by_year[yw];
    return `<b>ต.${t.name}</b> อ.${t.district}<br>` + (w * 625 < 50 ? "ภาพดาวเทียม 9 ปี ไม่ค่อยพบน้ำขังนานเกิน 1 เดือน" :
      `น้ำขังนานเกิน 1 เดือน อย่างน้อย 3 ใน 9 ปี: <b>${raiTxt(v)}</b><br>ปีที่หนักสุด ${beY(yw)}: ${raiTxt(w)} (${nf(w / t.area_km2 * 100)}% ของตำบล)`);
  }
  function histOverlay(on, fit = false) {
    if (on && S.ph) {
      if (!S.phLayer) { S.phLayer = L.imageOverlay(`data/static/${S.ph.png}`, S.ph.bounds, { opacity: .9, interactive: false }); layerCtl.addOverlay(S.phLayer, "เคยท่วมนาน ≥ 30 วัน (Sentinel-1 2560–68)"); }
      if (!map.hasLayer(S.phLayer)) S.phLayer.addTo(map);
      loadTam().then(g => { if (g && S.mode === "hist" && !map.hasLayer(g)) g.addTo(map); });
      if (fit) map.fitBounds(S.ph.bounds);
    } else {
      if (S.phLayer && map.hasLayer(S.phLayer)) map.removeLayer(S.phLayer);
      if (S.phTam && map.hasLayer(S.phTam)) map.removeLayer(S.phTam);
    }
  }
  function histLegend() {
    const j = S.ph; if (!j) return "<b>กำลังโหลด…</b>";
    return `<b>${S.view === "simple" ? "น้ำเคยขังนานเกิน 1 เดือน" : "ท่วม ≥ 30 วัน กี่ปีจาก 9 ปี"}</b>` +
      j.png_legend.map(([c, t]) => `<div class="row"><span class="sw" style="background:${c}"></span>${t}</div>`).join("") +
      `<div class="note" style="margin-top:3px">ภาพดาวเทียม ${beY(j.year_list?.[0] ?? 2017)}–${beY(Object.keys(j.years).slice(-1)[0])}</div>`;
  }
  function zoomTam(k) {
    loadTam().then(g => { if (!g) return; g.eachLayer(l => { if (l.feature.properties.k === k) { map.fitBounds(l.getBounds(), { padding: [30, 30] }); setTimeout(() => l.openTooltip(l.getBounds().getCenter()), 400); } }); });
  }
  // ประวัติของจุด/บ้าน เป็นประโยคภาษาง่าย
  function histLine(i) {
    const h = S.phHex; if (!h || i == null || i < 0) return null;
    const y = h.yrs[i], t = S.ph?.tambon?.[h.tam[i]], where = t ? `บริเวณนี้ (ต.${t.name})` : "บริเวณนี้";
    if (!y) return { w: 0, html: `ในอดีต: ภาพดาวเทียม ${h.n_years} ปี (${beY(h.years[0])}–${beY(h.years.slice(-1)[0])}) <b>ไม่พบน้ำขังนานเกิน 1 เดือน</b> ${where}` };
    return { w: HIST_W(y), html: `ในอดีต: ${where} เคยมีน้ำขังนานเกิน 1 เดือน <b>${y} ใน ${h.n_years} ปี</b> (${HIST_WORD(y)}) · ครั้งล่าสุดปี ${beY(h.last[i])}` };
  }
  function histSimpleHtml() {
    const j = S.ph; if (!j) return "";
    const ys = Object.keys(j.years), v30 = ys.map(y => j.years[y].prov_ge30d_km2), mx = Math.max(...v30);
    const big = ys.filter(y => j.years[y].prov_ge30d_km2 >= 0.6 * mx), bv = big.map(y => j.years[y].prov_ge30d_km2);
    const yrBars = ys.map(y => { const v = j.years[y].prov_ge30d_km2;
      return `<div class="h-yr"><span>ปี ${beY(y)}</span><i class="${big.includes(y) ? "big" : ""}" style="width:${v / mx * 100}%"></i><em>${raiTxt(v)}</em></div>`; }).join("");
    const ds = j.district.filter(d => d.ge30d_ge3y_km2 * 625 >= 300), dm = ds.length ? ds[0].ge30d_ge3y_km2 : 1;
    const dBars = ds.map(d => `<div class="h-yr" data-a="${d.district}"><span>อ.${d.district}</span><i class="big" style="width:${d.ge30d_ge3y_km2 / dm * 100}%"></i><em>${raiTxt(d.ge30d_ge3y_km2)}</em></div>`).join("");
    const top = j.tambon.map((t, k) => ({ ...t, k })).sort((a, b) => b.ge30d_ge3y_km2 - a.ge30d_ge3y_km2).slice(0, 10);
    const tBtns = top.map(t => { const w = t.mean_years_ge30d >= 3 ? 3 : 2;
      return `<button class="w${w}" data-k="${t.k}">ต.${t.name}<small>อ.${t.district} · ${raiTxt(t.ge30d_ge3y_km2)}</small></button>`; }).join("");
    const quiet = j.district.filter(d => d.ge30d_ge3y_km2 * 625 < 300).map(d => d.district);
    const hl = S.home != null && S.home >= 0 ? histLine(S.home) : null;
    return `<div class="s-card"><h2>ในอดีต ที่ไหนน้ำขังนาน? <span class="note" style="font-weight:400">ภาพดาวเทียม 9 ปี</span></h2>
      <p style="margin:0">ตั้งแต่ปี ${beY(ys[0])} ถึง ${beY(ys.slice(-1)[0])} มี <b>ปีน้ำมาก ${big.length} ปี</b> คือ ${big.map(beY).join(", ")} —
        แต่ละปีมีที่ดินที่ <b>น้ำขังนานเกิน 1 เดือน</b> ราว ${raiTxt(Math.min(...bv))} ถึง ${raiTxt(Math.max(...bv))}</p>
      ${hl ? `<div class="h-home w${hl.w}">🏠 บ้านของฉัน — ${hl.html}</div>` : ""}
      <div class="h-sub">ที่ดินที่น้ำขังนานเกิน 1 เดือน ในแต่ละปี (ทั้งจังหวัด)</div><div class="h-bars">${yrBars}</div>
      <div class="h-sub">อำเภอที่น้ำขังนานซ้ำ ๆ <span class="note" style="font-weight:400">(เกิน 1 เดือน อย่างน้อย 3 ใน 9 ปี)</span></div><div class="h-bars">${dBars}</div>
      ${quiet.length ? `<p class="s-small" style="margin:4px 0 0">แทบไม่พบน้ำขังนาน: ${quiet.map(d => "อ." + d).join(" · ")}</p>` : ""}
      <div class="h-sub">ตำบลที่น้ำขังนานบ่อยที่สุด</div><div class="h-tams">${tBtns}</div>
      <div class="s-row" style="margin-top:10px"><button class="s-btn" id="histMap">🗺️ ดูบนแผนที่</button><button class="s-btn ghost" id="histMore">ตารางทุกตำบล</button></div>
      <p class="s-hint">นี่คือ <b>สิ่งที่เคยเกิดขึ้น</b> ไม่ใช่การพยากรณ์ปีนี้ · น้ำที่ท่วมแล้วลดใน 1–2 สัปดาห์ (เช่น น้ำป่า) หรือน้ำใต้ต้นข้าวสูง ดาวเทียมอาจมองไม่เห็น ·
        ผลวิเคราะห์ละเอียดของแอ่งท่าตะโกอยู่ที่ <a href="#" id="toTk">แท็บท่าตะโก</a></p></div>`;
  }
  function wireHistSimple() {
    const on = (id, f) => { const e = $("#" + id); if (e) e.onclick = f; };
    // มือถือ: ย่อแผงก่อน แล้วค่อยซูม (ขนาดแผนที่เปลี่ยน)
    const go = fn => { setSimpleTime("hist"); if (innerWidth <= 760) $("#panel").classList.add("collapsed"); setTimeout(() => { map.invalidateSize(); fn(); }, 260); };
    on("histMap", () => go(() => map.fitBounds(S.ph.bounds)));
    on("histMore", () => { setView("expert"); $('.tabs button[data-tab="hist"]').click(); });
    $$("#simple .h-yr[data-a]").forEach(el => el.onclick = () => go(() => zoomAmphoe(el.dataset.a)));
    $$("#simple .h-tams button").forEach(el => el.onclick = () => go(() => zoomTam(+el.dataset.k)));
  }
  function histChartSvg(j) {
    const ys = Object.keys(j.years), W = 340, H = 130, pad = 26, bw = (W - pad) / ys.length;
    const mx = Math.max(...ys.map(y => j.years[y].prov_ge30d_km2), 1);
    const bars = ys.map((y, k) => { const a = j.years[y].prov_ge30d_km2, b = j.years[y].prov_ge60d_km2, x = pad + k * bw;
      const ha = a / mx * (H - 34), hb = b / mx * (H - 34);
      return `<rect x="${x + 3}" y="${H - 16 - ha}" width="${bw - 6}" height="${ha}" fill="#fdba74"><title>ปี ${beY(y)}: ท่วม ≥ 30 วัน ${nf(a)} กม² · ≥ 60 วัน ${nf(b)} กม² · ${j.years[y].n_img} ภาพ</title></rect>
        <rect x="${x + 3}" y="${H - 16 - hb}" width="${bw - 6}" height="${hb}" fill="#c2410c"/>
        <text x="${x + bw / 2}" y="${H - 20 - ha}" font-size="9" text-anchor="middle" fill="currentColor" opacity=".7">${nf(a)}</text>
        <text x="${x + bw / 2}" y="${H - 3}" font-size="9.5" text-anchor="middle" fill="currentColor" opacity=".7">${String(beY(y)).slice(2)}</text>`; }).join("");
    return `<svg viewBox="0 0 ${W} ${H}" style="color:var(--ink);width:100%"><text x="0" y="10" font-size="9" fill="currentColor" opacity=".6">กม²</text>
      <line x1="${pad}" x2="${W}" y1="${H - 16}" y2="${H - 16}" stroke="currentColor" opacity=".25"/>${bars}</svg>
      <div class="note"><span class="sw" style="background:#fdba74"></span>ท่วม ≥ 30 วัน <span class="sw" style="background:#c2410c;margin-left:8px"></span>≥ 60 วัน (ทั้งจังหวัด, ไม่รวมแหล่งน้ำถาวร)</div>`;
  }
  S.histAll = false;
  function renderHist() {
    const box = $("#histBox"); if (!box || !S.ph) return;
    const j = S.ph, ys = Object.keys(j.years), nImg = ys.reduce((a, y) => a + j.years[y].n_img, 0);
    const worst = r => { const o = r.ge60d_km2_by_year, y = Object.keys(o).reduce((a, b) => o[b] > o[a] ? b : a); return `${String(beY(y)).slice(2)}: ${nf(o[y], 1)}`; };
    const tam = j.tambon.map((t, k) => ({ ...t, k })).filter(t => S.histAll || t.ge30d_ge3y_km2 >= 1).sort((a, b) => b.ge30d_ge3y_km2 - a.ge30d_ge3y_km2);
    box.innerHTML = `<p class="note">น้ำท่วมจริงจากภาพเรดาร์ Sentinel-1 (ส.ค.–15 ธ.ค. ทุกปี, วงโคจร 62 ขาลง + 172 ขาขึ้น) ${nImg} การผ่าน ปี ${beY(ys[0])}–${beY(ys.slice(-1)[0])} ·
        ระยะเวลาท่วมคิดราย pixel ~36 ม. · ขอบเขตตำบล OCHA COD-AB</p>
      <div class="h-kpi"><div>ท่วม ≥ 30 วัน ≥ 3 ปี<b>${nf(j.prov_ge30d_ge3y_km2)} กม²</b></div><div>ท่วม ≥ 60 วัน ≥ 3 ปี<b>${nf(j.prov_ge60d_ge3y_km2)} กม²</b></div><div>แหล่งน้ำถาวร (ตัดออก)<b>${nf(j.perm_water_km2)} กม²</b></div></div>
      <div class="seg"><button id="histOn" class="${S.mode === "hist" ? "active" : ""}">แสดงบนแผนที่</button><button id="histOff">ซ่อน</button></div>
      <h3>พื้นที่ท่วมนานรายปี</h3>${histChartSvg(j)}
      <div id="pevBox"></div>
      <h3>รายอำเภอ</h3>
      <table class="tbl"><thead><tr><th>อำเภอ</th><th>≥30 วัน ≥3 ปี<br><small>กม²</small></th><th>≥60 วัน ≥3 ปี<br><small>กม²</small></th><th>ปีหนักสุด<br><small>≥60 วัน กม²</small></th></tr></thead>
      <tbody>${j.district.map(d => `<tr data-a="${d.district}"><td>${d.district}</td><td>${nf(d.ge30d_ge3y_km2, 1)}</td><td>${nf(d.ge60d_ge3y_km2, 1)}</td><td>${worst(d)}</td></tr>`).join("")}</tbody></table>
      <h3>รายตำบล ${S.histAll ? "(ทั้งหมด)" : "(ท่วม ≥ 30 วัน ≥ 3 ปี อย่างน้อย 1 กม²)"}</h3>
      <div style="overflow-x:auto"><table class="tbl"><thead><tr><th>ตำบล</th><th>≥30 วัน ≥3 ปี<br><small>กม²</small></th><th>≥60 วัน ≥3 ปี<br><small>กม²</small></th><th>ปีที่ท่วม ≥30 วัน<br><small>เฉลี่ยในตำบล</small></th><th>ปีหนักสุด<br><small>≥60 วัน กม²</small></th></tr></thead>
      <tbody>${tam.map(t => `<tr data-k="${t.k}"><td>${t.name}<div class="note">${t.district}</div></td><td>${nf(t.ge30d_ge3y_km2, 1)}</td><td>${nf(t.ge60d_ge3y_km2, 1)}</td><td>${nf(t.mean_years_ge30d, 1)}</td><td>${worst(t)}</td></tr>`).join("")}</tbody></table></div>
      <p><button class="chip" id="histAll">${S.histAll ? "แสดงเฉพาะตำบลที่ท่วมนาน" : `แสดงทั้ง ${j.tambon.length} ตำบล`}</button></p>
      <p class="note">${j.season} · ${j.method} · ข้อจำกัด: น้ำหลากที่ลดใน 1–2 สัปดาห์ (ที่ดอนตะวันตก/ตะวันออก) ภาพห่าง 6–12 วันจับไม่ทัน ; ป่า/ที่ลาดชัน/ใต้ต้นข้าวสูงตรวจน้ำไม่ได้ ;
        นาที่ขังน้ำตลอดฤดูแล้งถูกนับเป็นแหล่งน้ำถาวร ; ปี 2568 มี Sentinel-1C เพิ่ม (ภาพถี่ขึ้น)</p>`;
    $("#histOn").onclick = () => setMapMode("hist", true);
    $("#histOff").onclick = () => setMapMode("class");
    $("#histAll").onclick = () => { S.histAll = !S.histAll; renderHist(); };
    $$("#histBox tr[data-a]").forEach(r => r.onclick = () => { setMapMode("hist"); zoomAmphoe(r.dataset.a); });
    $$("#histBox tr[data-k]").forEach(r => r.onclick = () => { setMapMode("hist"); zoomTam(+r.dataset.k); });
    renderPev();
  }
  // ---------------------------------------------------------------- แบบจำลอง 2D ทั้งจังหวัด เทียบ Sentinel-1 (event_2d.py --hid prov_ev)
  S.pev = null; S.pevLayer = null; S.pevY = null;
  jOpt("data/static/prov_events.json", "s9").then(j => { S.pev = j; renderPev(); });
  function pevShow(y) {
    if (S.pevLayer) { map.removeLayer(S.pevLayer); layerCtl.removeLayer(S.pevLayer); S.pevLayer = null; }
    S.pevY = y;
    if (y && S.pev?.years?.[y]) {
      if (S.mode === "hist") setMapMode("class");
      S.pevLayer = L.imageOverlay(`data/static/${S.pev.years[y].png}`, S.pev.bounds, { opacity: .9, interactive: false }).addTo(map);
      layerCtl.addOverlay(S.pevLayer, `แบบจำลอง vs ดาวเทียม ปี ${+y + 543}`);
      map.fitBounds(S.pev.bounds);
    }
    renderPev(); renderLegend();
  }
  function renderPev() {
    const box = $("#pevBox"), j = S.pev; if (!box || !j || !Object.keys(j.years || {}).length) { if (box) box.innerHTML = ""; return; }
    const ys = Object.keys(j.years), y = S.pevY && j.years[S.pevY] ? S.pevY : null, v = y ? j.years[y] : null;
    const leg = [["#2563eb", "ตรงกัน"], ["#ea580c", "ท่วมจริงแต่จำลองไม่ถึง"], ["#facc15", "จำลองเกิน"], ["#94a3b8", "แหล่งน้ำถาวร"]]
      .map(([c, t]) => `<span class="sw" style="background:${c}"></span>${t}`).join(" ");
    const sumRow = yy => { const g = j.years[yy].ge30d;
      return `<tr data-y="${yy}"${yy === y ? ' style="background:var(--chip)"' : ""}><td>${+yy + 543}${j.years[yy].test_year ? '<div class="note">ทดสอบ</div>' : ""}</td><td>${nf(g.obs_km2)}</td><td>${nf(g.mod_km2)}</td><td>${nf(g.pod, 2)}</td><td>${nf(g.far, 2)}</td><td><b>${nf(g.csi, 2)}</b></td><td>${nf(j.years[yy].daily_csi_mean, 2)}</td></tr>`; };
    const dRows = v ? Object.entries(v.district).filter(([, g]) => g.obs_km2 >= 2 || g.mod_km2 >= 2).sort((a, b) => b[1].obs_km2 - a[1].obs_km2)
      .map(([d, g]) => `<tr data-a="${d}"><td>${d}</td><td>${nf(g.obs_km2)}</td><td>${nf(g.mod_km2)}</td><td>${nf(g.pod, 2)}</td><td>${nf(g.far, 2)}</td><td>${nf(g.csi, 2)}</td></tr>`).join("") : "";
    box.innerHTML = `<h3>แบบจำลอง 2D ทั้งจังหวัด เทียบน้ำท่วมจริง</h3>
      <p class="note">${j.note}</p>
      <table class="tbl"><thead><tr><th>ปี</th><th>ท่วม ≥30 วัน จริง<br><small>กม²</small></th><th>จำลอง<br><small>กม²</small></th><th>POD</th><th>FAR</th><th>CSI</th><th>CSI รายภาพ<br><small>เฉลี่ย</small></th></tr></thead>
      <tbody>${ys.map(sumRow).join("")}</tbody></table>
      <div class="seg" style="margin-top:6px">${ys.map(yy => `<button data-pev="${yy}" class="${yy === y ? "active" : ""}">แผนที่ปี ${String(+yy + 543).slice(2)}</button>`).join("")}<button data-pev="">ซ่อน</button></div>
      ${y ? `<p class="note" style="margin:6px 0">${leg}</p>
      <table class="tbl"><thead><tr><th>อำเภอ (ปี ${+y + 543})</th><th>จริง<br><small>กม²</small></th><th>จำลอง<br><small>กม²</small></th><th>POD</th><th>FAR</th><th>CSI</th></tr></thead><tbody>${dRows}</tbody></table>
      <table class="tbl" style="margin-top:8px"><thead><tr><th>ตำบล (ปี ${+y + 543})</th><th>จริง<br><small>กม²</small></th><th>จำลอง<br><small>กม²</small></th><th>CSI</th></tr></thead><tbody>${
        (j.tambon || []).filter(t => t.by_year[y] && (t.by_year[y].obs_km2 >= 1 || t.by_year[y].mod_km2 >= 1)).sort((a, b) => b.by_year[y].obs_km2 - a.by_year[y].obs_km2).slice(0, 25)
          .map(t => { const g = t.by_year[y]; return `<tr><td>${t.name}<div class="note">${t.district}</div></td><td>${nf(g.obs_km2, 1)}</td><td>${nf(g.mod_km2, 1)}</td><td>${nf(g.csi, 2)}</td></tr>`; }).join("")}</tbody></table>
      <p class="note">25 ตำบลที่ท่วมจริงมากที่สุด</p>` : ""}`;
    $$("#pevBox [data-pev]").forEach(b => b.onclick = () => pevShow(b.dataset.pev || null));
    $$("#pevBox tr[data-y]").forEach(r => r.onclick = () => pevShow(r.dataset.y));
    $$("#pevBox tr[data-a]").forEach(r => r.onclick = () => zoomAmphoe(r.dataset.a));
  }
  function setMapMode(m, fit = false) {
    S.mode = m; $("#mode").value = m; histOverlay(m === "hist", fit);
    if (m === "hist" && S.pevLayer) { map.removeLayer(S.pevLayer); layerCtl.removeLayer(S.pevLayer); S.pevLayer = null; S.pevY = null; }
    styleHex(); renderLegend(); if (S.ph) { const b = $("#histOn"); if (b) { b.classList.toggle("active", m === "hist"); } }
  }
  // ---------------------------------------------------------------- สถานการณ์สมมติ (ไม่ขึ้นกับฝนวันนี้): ฝน × ระดับน้ำแม่น้ำ
  S.scn = null; S.scnLayer = null;
  jOpt("data/static/hotspots/thatako_scenarios.json", "s5").then(j => { S.scn = j; tkRefresh(); });
  // ---------------------------------------------------------------- น้ำท่วมในอดีตจาก Sentinel-1 (2560–2568) เทียบกับแบบจำลอง
  S.s1h = null; S.s1Layer = null;
  jOpt("data/static/hotspots/thatako_s1_history.json", "s6").then(j => { S.s1h = j; tkRefresh(); });
  function s1Html() {
    const j = S.s1h; if (!j) return "";
    const be = y => String(+y + 543).slice(2);
    const ys = Object.keys(j.years);
    const c = j.compare_ge7d_vs_obs, E = c.over_r250, D = c.bank_r250;
    const leg = [["#bfdbfe", "1–2 ปี"], ["#60a5fa", "3–4"], ["#2563eb", "5–6"], ["#1e3a8a", "7–9"], ["#94a3b8", "แหล่งน้ำถาวร"]].map(([cc, t]) => `<span class="sw" style="background:${cc}"></span>${t}`).join(" ");
    return `<h3>เทียบกับน้ำท่วมจริง — Sentinel-1 ปี 2560–2568</h3>
      <p class="note">ภาพเรดาร์ ${ys.reduce((a, y) => a + j.years[y].n_img, 0)} ภาพ (ส.ค.–15 ธ.ค. ทุก 6–12 วัน) · พื้นที่ท่วม ≥ 30 วัน อย่างน้อย 3 ใน ${ys.length} ปี = <b>${nf(j.obs_ge30d_ge3y_km2, 0)}</b> กม² ในพื้นที่ศึกษา ·
        แบบจำลองสถานการณ์ E (ล้นตลิ่ง) ตรงกับที่ท่วมจริง POD ${nf(E.POD, 2)} · CSI ${nf(E.CSI, 2)} ; น้ำเท้อไม่ล้นตลิ่ง (D) POD ${nf(D.POD, 2)} · CSI ${nf(D.CSI, 2)}
        <br><button class="chip" data-s1="1">แผนที่ความถี่การท่วม</button> ${leg}</p>
      <p class="note">พื้นที่ท่วม ≥ 60 วัน (กม²) รายปี: ${ys.map(y => `${be(y)}: <b>${nf(j.years[y].aoi_ge60d_km2, 0)}</b>`).join(" · ")}</p>
      <div style="overflow-x:auto"><table class="tbl"><thead><tr><th>ตำบล</th><th>ท่วม ≥30 วัน ≥3 ปี กม²</th><th>ท่วม ≥60 วัน ≥3 ปี กม²</th><th>ระยะเวลาท่วม มัธยฐาน (วัน) ปี 60/64/65/67/68</th><th>แบบจำลอง E ขัง ≥7 วัน กม²</th></tr></thead>
      <tbody>${j.tambon.map(r => `<tr${r.in_existing ? ' style="opacity:.65"' : ""}><td>${r.name}<div class="note">${r.district}</div></td><td>${nf(r.ge30d_ge3y_km2, 1)}</td><td>${nf(r.ge60d_ge3y_km2, 1)}</td>
        <td>${Object.values(r.dur_med_d_big_years).map(v => v == null ? "–" : nf(v, 0)).join(" / ")}</td><td>${nf(r.model_ge7d_km2.over_r250, 1)}</td></tr>`).join("")}</tbody></table></div>
      <p class="note">${j.season} · น้ำใต้ต้นข้าว/พืชสูงตรวจไม่พบ (อาจนับต่ำในที่นา) · ${j.method}</p>`;
  }
  // ---------------------------------------------------------------- จำลองเหตุการณ์จริง 2564/2565/2568 เทียบ Sentinel-1 (event_2d.py → events_web.py)
  S.ev = null; S.evLayer = null;
  jOpt("data/static/hotspots/thatako_events.json", "s7").then(j => { S.ev = j; tkRefresh(); });
  function evChart(e) {
    const W = 300, H = 120, pl = 30, pb = 16, d = e.daily, n = d.length;
    const t0 = Date.parse(e.start), t1 = Date.parse(e.end), mx = Math.max(100, ...d.map(r => Math.max(r.obs_km2, r.mod_km2)));
    const X = s => pl + (Date.parse(s) - t0) / (t1 - t0) * (W - pl - 4), Y = v => H - pb - v / mx * (H - pb - 6);
    const line = (k, c, dash) => `<polyline fill="none" stroke="${c}" stroke-width="1.8" ${dash ? 'stroke-dasharray="4 2"' : ""} points="${d.map(r => `${X(r.date).toFixed(1)},${Y(r[k]).toFixed(1)}`).join(" ")}"/>` +
      d.map(r => `<circle cx="${X(r.date).toFixed(1)}" cy="${Y(r[k]).toFixed(1)}" r="2" fill="${c}"><title>${r.date}: ${nf(r[k], 0)} กม²</title></circle>`).join("");
    const mon = []; for (let m = new Date(e.start.slice(0, 8) + "01"); m <= new Date(e.end); m.setMonth(m.getMonth() + 1)) if (m >= new Date(e.start)) mon.push(new Date(m));
    const ax = mon.map(m => { const x = X(m.toISOString().slice(0, 10)); return `<line x1="${x}" x2="${x}" y1="4" y2="${H - pb}" stroke="var(--line)"/><text x="${x + 2}" y="${H - 4}" font-size="9" fill="currentColor">${m.toLocaleDateString("th-TH", { month: "short" })}</text>`; }).join("");
    const gy = [0, .5, 1].map(f => `<text x="${pl - 3}" y="${Y(mx * f) + 3}" font-size="9" text-anchor="end" fill="currentColor">${nf(mx * f, 0)}</text>`).join("");
    return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;height:auto">${ax}${gy}${line("obs_km2", "#2563eb")}${line("mod_km2", "#dc2626", 1)}</svg>`;
  }
  function evHtml() {
    const j = S.ev; if (!j) return "";
    const ys = Object.keys(j.years); if (!ys.length) return "";
    const be = y => +y + 543, e0 = j.years[ys[0]], p = e0.params || {};
    const leg = j._legend.map(([c, t]) => `<span class="sw" style="background:${c}"></span>${t}`).join(" ");
    const names = e0.tambon.map(r => r.name);
    const wl = ys.map(y => j.years[y].wl_max?.["N.67"] != null ? `${be(y)}: ${nf(j.years[y].wl_max["N.67"], 1)}` : "").filter(Boolean).join(" · ");
    return `<h3>จำลองเหตุการณ์จริงปี ${ys.map(be).join(" / ")} เทียบ Sentinel-1</h3>
      <p class="note">แบบจำลอง 2D โดเมนขยาย (cell 300 ม. ขึ้นเหนือถึง Y.5 โพทะเล) ใช้ระดับน้ำจริง Y.5 / N.67 / C.2 รายชั่วโมง + ฝน ERA5 ช่วง 20 ส.ค.–15 ธ.ค. ·
        เทียบภาพเรดาร์วันเดียวกัน (น้ำจำลองลึก ≥ ${nf(j._thr_m, 1)} ม.) · พารามิเตอร์: ตลิ่งใช้งาน = ตลิ่งสถานี ${+p.bank_off < 0 ? "−" : "+"}${nf(Math.abs(+p.bank_off), 1)} ม., น้ำเข้าทุ่ง ≤ ${nf(+p.in_cap_m3s, 0)} ลบ.ม./วิ,
        เก็บน้ำบึงบอระเพ็ด +${nf(+p.hold_level, 1)} ม., ระเหย+ซึม ${nf(+p.loss_mmh, 2)} มม./ชม., Manning ที่ราบ ×${nf(+p.n_mult, 0)}</p>
      <p class="note">⚠️ <b>N.67 ชุมแสงไม่ถึงตลิ่ง (28.21 ม.) ทั้ง 3 ปี</b> (สูงสุด ${wl} ม.) แต่ดาวเทียมเห็นท่วมนาน 2–3 เดือนทุกปี → น้ำเข้าทุ่ง/บึงผ่านคลอง ประตูน้ำ และช่องต่ำก่อนถึงระดับตลิ่ง</p>
      <div style="overflow-x:auto"><table class="tbl"><thead><tr><th>ปี</th><th>CSI รายวัน</th><th>ท่วม ≥30 วัน จำลอง / จริง กม²</th><th>POD</th><th>FAR</th><th>CSI</th><th>แผนที่</th></tr></thead>
      <tbody>${ys.map(y => { const e = j.years[y], g = e.ge30d; return `<tr><td>${be(y)}</td><td>${nf(e.csi_daily_mean, 2)}</td><td>${nf(g.mod_km2, 0)} / ${nf(g.obs_km2, 0)}</td>
        <td>${nf(g.POD, 2)}</td><td>${nf(g.FAR, 2)}</td><td><b>${nf(g.CSI, 2)}</b></td><td>${e.png ? `<button class="chip" data-ev="${y}">ดู</button>` : "–"}</td></tr>`; }).join("")}</tbody></table></div>
      <p class="note">${leg}</p>
      <p class="note">พื้นที่ท่วมรายวันในตำบลที่ศึกษา (กม²): <span style="color:#2563eb">● ดาวเทียม</span> <span style="color:#dc2626">■ แบบจำลอง</span></p>
      ${ys.map(y => `<div class="note" style="margin-top:4px"><b>ปี ${be(y)}</b></div>${evChart(j.years[y])}`).join("")}
      <div style="overflow-x:auto"><table class="tbl"><thead><tr><th>ตำบล</th>${ys.map(y => `<th>${be(y)}</th>`).join("")}</tr></thead>
      <tbody>${names.map((nm, i) => `<tr><td>${nm}</td>${ys.map(y => { const r = j.years[y].tambon[i]; return `<td>${nf(r.mod_ge30d_km2, 0)} / ${nf(r.obs_ge30d_km2, 0)}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>
      <p class="note">ตาราง = ท่วม ≥ 30 วัน จำลอง / จริง (กม²) · ที่ยังไม่ตรง: น้ำมาช้ากว่าจริง 1–2 สัปดาห์ในปี 65 และ 68 (น้ำก้อนแรกน่าจะมาจากพิจิตร/ยม และไพศาลี),
        ไผ่สิงห์ท่วมจริงนานกว่าแบบจำลองมาก, ปี 64 ช่วงพายุปลาย ก.ย. จำลองท่วมเกิน (น้ำตื้นในนาที่เรดาร์มองไม่เห็น), น้ำลดเร็วเกินช่วง ธ.ค. ปี 65 ·
        ขั้นต่อไป: ใช้ Q ของ Y.17/N.7A เป็นน้ำไหลเข้าจากเหนือ, burn ลำน้ำนอกจังหวัด, ระดับน้ำบึงบอระเพ็ด/การเปิดประตูจาก ชป.</p>`;
  }
  function scnHtml() {
    const j = S.scn; if (!j) return "";
    const keys = Object.keys(j).filter(k => !k.startsWith("_")); if (!keys.length) return "";
    const lbl = k => `ฝน ${nf(j[k].rain_mm)} มม./${j[k].rain_days} วัน · แม่น้ำ${j[k].river}`;
    const names = j[keys[0]].ponding.tambon.map(r => r.name);
    const cell = (k, i) => { const r = j[k].ponding.tambon[i]; return `<td title="ท่วมสูงสุด ${nf(r.wet_now_km2, 1)} กม² · ขัง ≥ 7 วัน ${nf(r.wet_ge7d_km2, 1)} กม² · ยังไม่ลดวันสุดท้าย ${nf(r.wet_ge14d_km2, 1)} กม²">${nf(r.wet_now_km2, 0)}<div class="note" style="white-space:nowrap">${nf(r.wet_ge7d_km2, 0)}</div></td>`; };
    return `<h3>สถานการณ์สมมติ — แอ่งท่าตะโก (จำลอง ${j[keys[0]].days} วัน เริ่มจากแห้ง)</h3>
      <p class="note">แยกผลของ “ฝนในพื้นที่” กับ “น้ำเท้อจากแม่น้ำน่าน/เจ้าพระยา” · ช่องตาราง = พื้นที่ท่วมสูงสุดที่เป็นผืน (กม²) / บรรทัดล่าง = ขัง ≥ 7 วัน (กม²) · แผนที่เวลาน้ำลดนับจากฝนเริ่มตก · กดปุ่มเพื่อดูแผนที่</p>
      ${keys.map((k, n) => `<div class="note" style="margin:3px 0"><b>${String.fromCharCode(65 + n)}</b> ${lbl(k)} ·
        <button class="chip" data-scn="${k}:max">ความลึกสูงสุด</button> <button class="chip" data-scn="${k}:rem">เวลาน้ำลด</button></div>`).join("")}
      <div style="overflow-x:auto"><table class="tbl"><thead><tr><th>ตำบล</th>${keys.map((k, n) => `<th>${String.fromCharCode(65 + n)}</th>`).join("")}</tr></thead>
      <tbody>${names.map((nm, i) => `<tr${j[keys[0]].ponding.tambon[i].in_existing ? ' style="opacity:.65"' : ""}><td>${nm}</td>${keys.map(k => cell(k, i)).join("")}</tr>`).join("")}</tbody></table></div>`;
  }
  $$("#hotSeg button").forEach(b => b.onclick = () => { S.hotSnap = b.dataset.snap; $$("#hotSeg button").forEach(x => x.classList.toggle("active", x === b)); renderHot(); });

  // ---------------------------------------------------------------- rain chart (SVG)
  function renderRain() {
    const s = S.series, box = $("#rainChart"); if (!s) { box.innerHTML = ""; return; }
    const W = 340, H = 140, pad = 22, n = s.t.length, mx = Math.max(1, ...s.mean);
    const bw = (W - pad) / n, iNow = s.t.indexOf(s.now);
    let bars = "";
    s.mean.forEach((v, k) => {
      const h = (v / mx) * (H - 30); const fc = k > iNow;
      bars += `<rect x="${pad + k * bw}" y="${H - 16 - h}" width="${Math.max(bw - .3, .6)}" height="${h}" fill="${fc ? "#a78bfa" : "#3b82f6"}"><title>${fmtT(s.t[k])}: ${v} มม.</title></rect>`;
    });
    const xNow = pad + iNow * bw;
    const days = s.t.map((t, k) => [t, k]).filter(([t]) => new Date((t + 7 * 3600) * 1000).getUTCHours() === 0);
    const ticks = days.map(([t, k]) => `<line x1="${pad + k * bw}" x2="${pad + k * bw}" y1="${H - 16}" y2="${H - 12}" stroke="currentColor" opacity=".4"/><text x="${pad + k * bw}" y="${H - 2}" font-size="9" text-anchor="middle" fill="currentColor" opacity=".6">${new Date(t * 1000).toLocaleDateString("th-TH", { timeZone: "Asia/Bangkok", day: "numeric", month: "short" })}</text>`).join("");
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="color:var(--ink)">
      <text x="0" y="10" font-size="9" fill="currentColor" opacity=".6">${nf(mx, 1)} มม./ชม.</text>
      <line x1="${pad}" x2="${W}" y1="${H - 16}" y2="${H - 16}" stroke="currentColor" opacity=".25"/>
      ${bars}${ticks}<line x1="${xNow}" x2="${xNow}" y1="12" y2="${H - 16}" stroke="#dc2626" stroke-dasharray="3 2"/>
      <text x="${xNow + 3}" y="20" font-size="9" fill="#dc2626">ตอนนี้</text></svg>
      <div class="note"><span class="sw" style="background:#3b82f6"></span>ที่ผ่านมา <span class="sw" style="background:#a78bfa;margin-left:8px"></span>พยากรณ์</div>`;
    $("#adjustNote").textContent = S.meta.adjust || "";
  }

  // ---------------------------------------------------------------- about
  function renderAbout() {
    const m = S.meta, src = m.sources || {};
    const ok = k => src[k]?.ok ? "✅" : "⚠️";
    $("#about").innerHTML = `
      <h4>สถานะแหล่งข้อมูลรอบล่าสุด</h4><ul>
        <li>${ok("openmeteo")} Open-Meteo ฝนรายชั่วโมง ${src.openmeteo?.points || "–"} จุด grid</li>
        <li>${ok("thaiwater_rain")} ThaiWater ฝน 24 ชม. ${src.thaiwater_rain?.used ?? "–"} สถานีที่ใช้ปรับแก้</li>
        <li>${ok("thaiwater_level")} ThaiWater ระดับน้ำ ${src.thaiwater_level?.stations ?? "–"} สถานี</li>
        <li>${ok("upstream")} ลุ่มน้ำต้นน้ำ ${src.upstream?.zones ?? "–"} zones · ${src.upstream?.entries ?? "–"} จุดน้ำเข้า</li>
        <li>${ok("gistda")} GISTDA น้ำท่วมจากดาวเทียม ${src.gistda?.ok ? src.gistda.features + " แปลง" : "(" + (src.gistda?.error || "ปิด") + ")"}</li>
        <li>${S.s1status?.ok ? "✅" : "⚠️"} Sentinel-1 SAR (Planetary Computer, ไม่ต้องใช้ key) ${S.s1status?.latest ? "ภาพล่าสุด " + new Date(S.s1status.latest.t_acq * 1000).toLocaleString("th-TH", { timeZone: "Asia/Bangkok", dateStyle: "medium", timeStyle: "short" }) + " · ท่วม " + S.s1status.latest.km2 + " กม² · " + S.s1status.records + " ภาพสะสม" : "(" + (S.s1status?.error || "ยังไม่มีภาพ") + ")"}</li></ul>
      <p class="note">โมเดล: ${m.model} · ใช้เวลา ${m.runtime_s} วินาที</p>
      <h4>ขั้นตอนวิเคราะห์</h4>
      <p><b>ภูมิประเทศ (ArcGIS Pro)</b> — FABDEM 30 ม. (Copernicus DEM ที่ตัดอาคาร/ต้นไม้ออก) + burn ลำน้ำ/คลองจาก OpenStreetMap + ยกคันกั้นน้ำ → Fill, Flow Direction, Flow Accumulation, HAND, ความลาดชัน; ESA WorldCover → Curve Number; สรุปลง hex 1 กม² พร้อม<b>เครือข่ายการไหล</b>: สัดส่วนการไหลไปยัง hex ข้างเคียงหลายทิศ (จากเส้นทางน้ำ D8 ทุก cell ที่ข้ามขอบ hex), <b>ระดับจุดล้น</b>ระหว่าง hex (P5 ของความสูงตามแนวขอบ) และ<b>ความสัมพันธ์ระดับ-ปริมาตร</b>ของแต่ละ hex (ความสูง P0–P100)</p>
      <p><b>ทุก 1 ชั่วโมง</b> — ฝนรายชั่วโมง 30 วัน + พยากรณ์ 72 ชม. (Open-Meteo ปรับแก้ด้วยสถานี ThaiWater) → SCS-CN → น้ำเก็บในคันนา/แอ่ง → <b>ระบายตามทิศการไหลหลายทิศ</b> (linear reservoir, เร็วขึ้นตามความยาวคลองใน hex) + ท่อระบายน้ำในเขตเมือง + <b>สถานีสูบน้ำ</b>ส่งลงแม่น้ำ; <b>ประตูระบายน้ำปิด</b>และการระบายช้าลงเมื่อระดับน้ำในแม่น้ำใกล้ตลิ่ง → ทุก 15 นาที <b>น้ำไหลข้าม hex ตามระดับผิวน้ำเหนือจุดล้น</b> (fill-spill แบบ weir — น้ำแผ่ด้านข้างและไหลย้อนได้, คันกั้นน้ำ/ถนนเป็นจุดล้นที่สูง) → ความลึกเฉลี่ยและสัดส่วนพื้นที่ท่วมใน hex</p>
      <p><b>น้ำจาก 4 จังหวัดต้นน้ำ</b> — DEM ภูมิภาคหาพื้นที่ที่ไหลเข้า นว. จุดน้ำเข้า และเวลาเดินทาง → hydrograph จากฝน (SCS-CN + lag + linear reservoir) ปรับขนาดด้วยปริมาณน้ำจริงของกรมชลประทาน; แม่น้ำสายหลักพยากรณ์ Q = ค่าตรวจวัด + การเปลี่ยนแปลงจากฝนต้นน้ำ ส่วนที่เกินความจุลำน้ำ<b>ฉีดเข้า hex ลำน้ำหลัก</b>แล้วให้ fill-spill กระจายตามระดับผิวน้ำและคันกั้นน้ำ; ลำน้ำสาขาไหลเข้าเครือข่าย hex ตรงจุดน้ำเข้า</p>
      <p><b>แบบจำลอง 2 มิติจุดวิกฤต</b> — local inertial rain-on-grid 120 ม. (ลาดยาว, เมืองนครสวรรค์, ชุมแสง) ทุก 6 ชม.; ใช้สภาพน้ำจากโมเดล hex เป็นเงื่อนไขเริ่มต้นและระดับน้ำสถานีเป็นขอบเขตแม่น้ำ; ผลใช้แสดงรายละเอียดและ<b>สอบเทียบ</b>พารามิเตอร์ของโมเดล hex (pipeline/calibrate.py); มีชุดข้อมูลสำหรับ HEC-RAS 2D ในโฟลเดอร์ hecras/</p>
      <p><b>ระยะเวลาท่วม</b> — ชั่วโมงที่ความลึก ≥ 10 ซม. ต่อเนื่องถึงปัจจุบัน และเวลาที่คาดว่าจะลดต่ำกว่า 10 ซม. จากการจำลองต่อ (เกิน 72 ชม. ใช้อัตราการลดช่วงท้าย สูงสุด 14 วัน) · พื้นที่ที่ท่วมจากน้ำล้นตลิ่ง นับจากเวลาที่สถานีหลักของลำน้ำนั้นขึ้นเหนือตลิ่ง (ประวัติรายชั่วโมง 30 วัน) · ปริมาณน้ำในลำน้ำในอนาคตไม่ถือว่าคงที่ แต่ลดลงตามอัตราน้ำลด (recession) ที่วิเคราะห์จากช่วงน้ำลดจริงของแต่ละสถานี</p>
      <h4>ข้อจำกัด</h4><ul>
        <li>โมเดล hex เป็นแบบจำลองเชิงคัดกรองความละเอียด 1 กม² — ใช้ผล 2D ในจุดวิกฤตประกอบ และควรสอบเทียบกับพื้นที่ท่วมจริง (GISTDA) ก่อนใช้ตัดสินใจ</li>
        <li>ข้อมูลโครงสร้างระบายน้ำมาจาก OpenStreetMap ซึ่งอาจไม่ครบ/ไม่มีความจุจริง — เพิ่มข้อมูลจากกรมชลประทาน/เทศบาลได้ใน drainage_assets_user.csv</li>
        <li>ไม่ได้พยากรณ์การระบายเขื่อนภูมิพล/สิริกิติ์ (อยู่ในค่าตรวจวัดปัจจุบันของสถานี)</li>
        <li>ฝนย้อนหลังเป็นค่าจากแบบจำลองอากาศ ปรับแก้เฉพาะ 24 ชม. ล่าสุด</li></ul>
      <h4>แหล่งข้อมูล</h4><ul>
        <li>Open-Meteo (CC BY 4.0) · ThaiWater / สสน. · GISTDA · Windy.com</li>
        <li>Copernicus Sentinel-1 RTC (Microsoft Planetary Computer) — น้ำท่วมจริงย้อนหลัง 2560–2568 · ขอบเขตตำบล OCHA COD-AB (CC BY-IGO)</li>
        <li>FABDEM V1-2 (Hawker et al. 2022, CC BY-NC-SA 4.0) · Copernicus DEM GLO-30 (© DLR/Airbus, ESA) · ESA WorldCover 2021 (CC BY 4.0) · OpenStreetMap (ODbL) · geoBoundaries</li></ul>`;
  }

  // ---------------------------------------------------------------- โหมดดูแบบง่าย (สำหรับประชาชนทั่วไป)
  // ภาษาง่าย: ความลึกเทียบกับร่างกาย, พื้นที่เป็นไร่, เวลาเป็นวัน, สีสัญญาณไฟ ; ข้อมูลชุดเดียวกับโหมดละเอียด
  const DEPTH_WORD = ["ไม่มีน้ำท่วม", "ระดับข้อเท้า", "ระดับเข่า", "ระดับเอว", "สูงกว่าเอว"];
  const DEPTH_CM = ["", "10–25 ซม.", "25–50 ซม.", "50 ซม.–1 ม.", "เกิน 1 เมตร"];
  const LV = [
    { t: "ปกติ", big: "ยังไม่มีน้ำท่วมที่น่ากังวล" },
    { t: "เฝ้าระวัง", big: "มีน้ำท่วมบางพื้นที่" },
    { t: "เตือนภัย", big: "น้ำท่วมหลายพื้นที่" },
    { t: "อันตราย", big: "น้ำท่วมหนัก" }];
  const WATER_Y = [84, 75, 63, 46, 28];            // ระดับน้ำบนรูปคน (ข้อเท้า เข่า เอว อก)
  const person = (c, cls = "") => `<svg viewBox="0 0 60 84" class="${cls}" aria-hidden="true">
    <circle cx="30" cy="10" r="7" fill="#64748b"/>
    <path d="M22 20H38Q42 20 42 24V46H37V82H31V56H29V82H23V46H18V24Q18 20 22 20Z" fill="#64748b"/>
    ${c > 0 ? `<rect x="0" y="${WATER_Y[c]}" width="60" height="${84 - WATER_Y[c]}" fill="${c >= 4 ? "#1e40af" : "#3b82f6"}" fill-opacity=".62"/>
    <path d="M0 ${WATER_Y[c]} q7.5 -3 15 0 t15 0 t15 0 t15 0" fill="none" stroke="#1d4ed8" stroke-width="1.6"/>` :
    `<line x1="4" y1="83" x2="56" y2="83" stroke="#94a3b8" stroke-width="2"/>`}</svg>`;
  const raiTxt = km2 => {
    const r = (km2 || 0) * 625;                    // 1 ตร.กม. = 625 ไร่
    if (r < 50) return "ไม่ถึง 50 ไร่";
    if (r >= 1e6) return `${nf(r / 1e6, 1)} ล้านไร่`;
    if (r >= 1e5) return `${nf(r / 1e5, 1)} แสนไร่`;
    if (r >= 1e4) return `${nf(r / 1e4, 1)} หมื่นไร่`;
    return `${nf(Math.round(r / 100) * 100)} ไร่`;
  };
  const sinceTxt = h => h == null || h < 0 ? "ไม่ทราบ" : h < 1 ? "เพิ่งเริ่ม" : h < 24 ? `${h} ชั่วโมง` : `ประมาณ ${Math.round(h / 24)} วัน`;
  const leftTxt = h => h == null || h < 0 ? "ไม่ทราบ" : h >= 999 ? "นานกว่า 2 สัปดาห์" : h <= 12 ? "ภายในครึ่งวัน" : h <= 24 ? "ภายใน 1 วัน" : `ประมาณ ${Math.round(h / 24)} วัน`;
  const dayTxt = unix => new Date(unix * 1000).toLocaleDateString("th-TH", { timeZone: "Asia/Bangkok", weekday: "long", day: "numeric", month: "short" });
  const trendOf = (a, b) => b > a * 1.1 + 0.5 ? { c: "up", i: "▲", t: "น้ำจะเพิ่มขึ้น" } : b < a * 0.9 - 0.5 ? { c: "down", i: "▼", t: "น้ำจะลดลง" } : { c: "flat", i: "■", t: "ใกล้เคียงเดิม" };
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { } } };

  function provLevel() {
    const s = S.meta.summary, a = s.km2_watch || 0;
    let lv = a < 2 ? 0 : a < 100 ? 1 : a < 500 ? 2 : 3;
    if (lv === 0 && (s.wl_over_bank > 0 || (s.km2_72h || 0) >= 2)) lv = 1;
    return lv;
  }
  function rainWord(mm) { return mm < 5 ? "แทบไม่มีฝน" : mm < 20 ? "ฝนเล็กน้อย" : mm < 50 ? "ฝนปานกลาง" : mm < 100 ? "ฝนหนัก" : "ฝนหนักมาก"; }
  function nearestHex(lat, lon) {
    const p = S.params; let best = -1, bd = 1e9;
    const kx = Math.cos(lat * Math.PI / 180);
    for (let i = 0; i < p.n; i++) { const dx = (p.lon[i] - lon) * kx, dy = p.lat[i] - lat, d = dx * dx + dy * dy; if (d < bd) { bd = d; best = i; } }
    return Math.sqrt(bd) * 111 < 1.2 ? best : -1;   // ห่างจาก hex ใกล้สุดเกิน ~1 กม. = อยู่นอกจังหวัด
  }
  function frameAtDay(d) {
    const tgt = S.frames.now + d * 86400; let k = 0, bd = Infinity;
    S.frames.t.forEach((t, i) => { const x = Math.abs(t - tgt); if (x < bd) { bd = x; k = i; } });
    return k;
  }

  // จำนวน hex ที่มีน้ำ (≥ 10 ซม.) ในเฟรม k ทั้งจังหวัดและรายอำเภอ — ใช้บอกแนวโน้ม "อีก 3 วัน"
  function floodCount(k) {
    const f = S.frames.c[k], p = S.params, a = new Array(p.amphoe_list.length).fill(0); let all = 0;
    for (let i = 0; i < f.length; i++) if (f.charCodeAt(i) > 48) { all++; a[p.amph[i]]++; }
    return { all, a };
  }
  // ความลึก "ส่วนใหญ่" ของแต่ละอำเภอ = ชั้นมัธยฐานถ่วงพื้นที่ท่วม (ตัวเลขลึกสุดอย่างเดียวทำให้ดูน่ากลัวเกินจริง)
  function typicalDepth() {
    const st = S.status, p = S.params, acc = p.amphoe_list.map(() => [0, 0, 0, 0, 0]);
    for (let i = 0; i < st.n; i++) if (st.c[i] > 0) acc[p.amph[i]][st.c[i]] += st.f ? Math.max(st.f[i], 1) : 100;
    return acc.map(w => { const tot = w.reduce((x, y) => x + y, 0); if (!tot) return 0; let c = 0, s = 0; for (let k = 1; k < 5; k++) { s += w[k]; if (s >= tot / 2) { c = k; break; } } return c; });
  }
  // ข้อมูลของจุดหนึ่ง (hex) เป็นประโยคภาษาง่าย
  function placeInfo(i) {
    const st = S.status, p = S.params, isNow = S.frames.t[S.frame] === S.frames.now;
    const c = isNow ? st.c[i] : +S.frames.c[S.frame][i];
    const amp = p.amphoe_list[p.amph[i]];
    const cause = st.s[i] === 1 ? "น้ำฝนตกหนักแล้วระบายไม่ทัน" : st.s[i] === 2 ? "น้ำจากแม่น้ำ/ลำน้ำล้นตลิ่ง" : st.s[i] === 3 ? "ทั้งน้ำฝนขังและน้ำจากแม่น้ำล้นตลิ่ง" : "";
    const when = isNow ? "ตอนนี้" : `วัน${dayTxt(S.frames.t[S.frame]).replace(/^วัน/, "")}`;
    const lines = [];
    if (c > 0) {
      if (isNow) {
        lines.push(`ท่วมมาแล้ว <b>${sinceTxt(st.h[i])}</b> · น่าจะลดใน <b>${leftTxt(st.r[i])}</b>`);
        if (cause) lines.push(`สาเหตุ: ${cause}`);
      }
    } else if (st.m72[i] > 0) lines.push(`<b class="up">อีก 3 วันข้างหน้าอาจมีน้ำท่วม${DEPTH_WORD[st.m72[i]]}</b>`);
    if (c > 0 && isNow && st.m72[i] > c) lines.push(`<b class="up">3 วันข้างหน้าอาจสูงขึ้นถึง${DEPTH_WORD[st.m72[i]]}</b>`);
    if (p.f_low[i] > 0.5 && c === 0) lines.push("เป็นที่ลุ่มต่ำ น้ำท่วมง่ายกว่าบริเวณรอบ ๆ");
    lines.push(`ฝนตกแล้ว 24 ชม.: ${nf(st.p24[i])} มม. (${rainWord(st.p24[i])}) · 3 วันข้างหน้า: ${nf(st.f72[i])} มม. (${rainWord(st.f72[i])})`);
    const hl = histLine(i); if (hl) lines.push(hl.html);
    const sp = isNow ? "" : " ";
    const head = c > 0 ? `${when}${sp}น้ำท่วม${DEPTH_WORD[c]}` : `${when}${sp}ไม่มีน้ำท่วมขัง`;
    const sub = c > 0 ? `ประมาณ ${DEPTH_CM[c]}${isNow && st.d[i] > 0 ? ` (เฉลี่ย ${nf(st.d[i])} ซม.)` : ""}` : "";
    return { c, amp, head, sub, lines };
  }
  function simplePopup(i, latlng) {
    const q = placeInfo(i);
    const html = `<div class="pp"><h4>จุดที่เลือก · อ.${q.amp}</h4>
      <div class="pbig">${person(q.c)}<div><b>${q.head}</b><span class="note">${q.sub}</span></div></div>
      ${q.lines.map(x => `<p>${x}</p>`).join("")}
      <p class="note">เป็นค่าเฉลี่ยของพื้นที่ราว 1 ตร.กม. รอบจุดนี้ บ้านที่อยู่สูงหรือต่ำกว่าอาจต่างไป</p>
      <p><a data-home="${i}">บันทึกเป็น “บ้านของฉัน”</a> · <a data-detail="${i}">ดูข้อมูลละเอียด</a></p></div>`;
    const pop = L.popup({ maxWidth: 300 }).setLatLng(latlng).setContent(html).openOn(map);
    const el = pop.getElement();
    el.querySelector("[data-home]").onclick = () => { setHome(i); map.closePopup(); };
    el.querySelector("[data-detail]").onclick = () => { setView("expert"); hexPopup(i, latlng); };
  }

  let homeMk = null;
  function setHome(i, fly = false) {
    S.home = i; store.set("nsf_home", i == null ? null : String(i));
    if (homeMk) { map.removeLayer(homeMk); homeMk = null; }
    if (i != null && i >= 0) {
      const ll = [S.params.lat[i], S.params.lon[i]];
      homeMk = L.marker(ll, { title: "บ้านของฉัน", icon: L.divIcon({ className: "", iconSize: [30, 30], iconAnchor: [15, 28],
        html: `<svg viewBox="0 0 30 30" width="30" height="30"><path d="M15 2 2 13h4v14h7v-8h4v8h7V13h4z" fill="#dc2626" stroke="#fff" stroke-width="2"/></svg>` }) }).addTo(map);
      homeMk.on("click", e => simplePopup(i, e.latlng));
      if (fly) map.setView(ll, 12);
    }
    renderSimple();
  }
  function locateMe() {
    const msg = $("#homeMsg");
    if (!navigator.geolocation) { msg.textContent = "เครื่องนี้หาตำแหน่งไม่ได้ ให้แตะบนแผนที่ตรงบ้านของคุณแทน"; return; }
    msg.textContent = "กำลังหาตำแหน่ง…";
    navigator.geolocation.getCurrentPosition(pos => {
      const i = nearestHex(pos.coords.latitude, pos.coords.longitude);
      if (i < 0) { msg.textContent = "ตำแหน่งของคุณอยู่นอกจังหวัดนครสวรรค์ ให้แตะบนแผนที่ตรงบ้านที่ต้องการดูแทน"; return; }
      setHome(i, true);
    }, () => { msg.textContent = "ไม่ได้รับอนุญาตให้ใช้ตำแหน่ง ให้แตะบนแผนที่ตรงบ้านของคุณแทน"; }, { enableHighAccuracy: true, timeout: 12000 });
  }

  function renderSimple() {
    const box = $("#simple"); if (!box || !S.meta || !S.status) return;
    const s = S.meta.summary, lv = provLevel(), du = s.duration || {};
    const dist = (S.districts || []).slice().sort((a, b) => (b.km2_watch || 0) - (a.km2_watch || 0));
    const hit = dist.filter(d => (d.km2_watch || 0) >= 0.5), calm = dist.filter(d => (d.km2_watch || 0) < 0.5);
    const st = S.status, p = S.params;
    const cNow = floodCount(S.frames.t.indexOf(S.frames.now)), c72 = floodCount(frameAtDay(3));
    const tr = trendOf(cNow.all, c72.all), typ = typicalDepth();
    let f72 = 0, p24 = 0, n = 0;
    for (let i = 0; i < st.n; i++) if (!(p.f_water[i] > 0.5)) { f72 += st.f72[i]; p24 += st.p24[i]; n++; }
    f72 /= n || 1; p24 /= n || 1;

    // 1) ภาพรวมจังหวัด
    let heroTxt = lv === 0 ? "ทั้งจังหวัดยังไม่มีน้ำท่วมขังที่น่ากังวล ติดตามข่าวสารตามปกติ" :
      `ตอนนี้มีน้ำท่วมใน <b>${hit.length} อำเภอ</b>${hit[0] ? ` มากที่สุดที่ <b>อ.${hit[0].amphoe}</b>` : ""}`;
    if (lv > 0 && du.r_med != null) heroTxt += `<br>พื้นที่ส่วนใหญ่น่าจะลดใน <b>${leftTxt(du.r_med)}</b>`;
    const hero = `<div class="s-card s-hero lv${lv}">
      <div class="lv">สถานการณ์ จ.นครสวรรค์ · ระดับ “${LV[lv].t}”</div>
      <div class="big">${LV[lv].big}</div><p>${heroTxt}</p>
      <div class="facts">
        <div>พื้นที่น้ำท่วม<b>${raiTxt(s.km2_watch)}</b></div>
        <div>3 วันข้างหน้า<b>${tr.i} ${tr.t.replace("น้ำจะ", "")}</b></div>
        <div>ฝน 3 วันข้างหน้า<b>${rainWord(f72).replace("ฝน", "") || "–"}</b></div>
      </div></div>`;

    // 2) บ้านของฉัน
    let homeHtml;
    if (S.home != null && S.home >= 0) {
      const q = placeInfo(S.home);
      homeHtml = `<div class="s-place">${person(q.c)}<div class="txt"><b>${q.head}</b><span>${q.sub ? q.sub + " · " : ""}อ.${q.amp}</span></div></div>
        ${q.lines.map(x => `<p class="s-small" style="margin:4px 0;color:var(--ink)">${x}</p>`).join("")}
        <div class="s-row" style="margin-top:8px"><button class="s-btn ghost" id="homeGo">ดูบนแผนที่</button><button class="s-btn ghost" id="homeLoc">ใช้ตำแหน่งปัจจุบัน</button><button class="s-btn ghost" id="homeClr">ลบ</button></div>`;
    } else {
      homeHtml = `<p style="margin:0 0 8px">ดูว่าบ้านของคุณน้ำท่วมไหม ลึกแค่ไหน และอีกกี่วันจะลด</p>
        <div class="s-row"><button class="s-btn" id="homeLoc">📍 ใช้ตำแหน่งของฉัน</button></div>
        <p class="s-hint">หรือ <b>แตะบนแผนที่</b> ตรงหมู่บ้านของคุณ แล้วกด “บันทึกเป็นบ้านของฉัน”</p>`;
    }
    homeHtml += `<p class="s-hint" id="homeMsg"></p>`;

    // 3) รายอำเภอ
    const ampRow = d => {
      const ai = p.amphoe_list.indexOf(d.amphoe), t = trendOf(cNow.a[ai] || 0, c72.a[ai] || 0);
      const ty = typ[ai] || Math.min(d.max_cls_now, 1), mx = d.max_cls_now;
      return `<div class="s-amp" data-a="${d.amphoe}">${person(ty)}<div><div class="nm">อ.${d.amphoe}</div>
        <div class="ds">น้ำท่วม ${raiTxt(d.km2_watch)} · ส่วนใหญ่${ty ? DEPTH_WORD[ty] : "ไม่ถึงข้อเท้า"}${mx > ty ? ` (บางจุด${DEPTH_WORD[mx]})` : ""}${d.dur_km2 > 0 ? `<br>ส่วนใหญ่น่าจะลดใน ${leftTxt(d.r_med)}` : ""}</div></div>
        <div class="tr ${t.c}">${t.i} ${t.t.replace("น้ำจะ", "")}<br><span class="note">3 วันข้างหน้า</span></div></div>`;
    };
    const ampHtml = (hit.length ? hit.map(ampRow).join("") : `<p style="margin:0">ยังไม่มีอำเภอที่มีน้ำท่วมขัง</p>`) +
      (calm.length ? `<p class="s-small" style="margin:8px 0 0">อำเภอที่ยังไม่มีน้ำท่วม: ${calm.map(d => d.amphoe).join(" · ")}</p>` : "");

    // 4) แม่น้ำ
    const rv = (S.upstream?.rivers || []).map(r => {
      const pct = r.storage_pct ?? (r.qmax ? r.q_obs / r.qmax * 100 : null);
      const col = r.overbank_now || pct > 100 ? "#dc2626" : pct >= 80 ? "#d97706" : "#16a34a";
      const word = r.overbank_now || pct > 100 ? "ล้นตลิ่ง" : pct >= 80 ? "ใกล้เต็มตลิ่ง" : "ยังรับน้ำได้";
      const rel = r.q_obs ? (r.recession?.trend_qph || 0) / r.q_obs : 0;
      const tw = rel > 0.002 ? `<span class="up">▲ น้ำกำลังขึ้น</span>` : rel < -0.002 ? `<span class="down">▼ น้ำกำลังลด</span>` : `<span class="flat">■ ทรงตัว</span>`;
      let more = "";
      if (r.overbank_now) more = `ล้นตลิ่งมาแล้ว ${sinceTxt(r.overbank_since ? Math.round((S.frames.now - r.overbank_since) / 3600) : -1)}` + (r.h_below_bank > 0 ? ` · คาดว่าจะลดต่ำกว่าตลิ่งใน ${leftTxt(r.h_below_bank)}` : "");
      else if (r.peak_t > S.frames.now + 3 * 3600 && r.peak_q > r.q_obs * 1.03) more = `คาดว่าน้ำจะขึ้นสูงสุดราว${dayTxt(r.peak_t)} ${r.qmax && r.peak_q > r.qmax ? `<b class="up">อาจล้นตลิ่ง</b>` : "แต่ยังไม่ถึงตลิ่ง"}`;
      const w = Math.max(0, Math.min(pct || 0, 130)) / 130 * 100;
      return `<div class="s-river"><div class="t"><span>${r.name}</span><span class="pill" style="background:${col}">${word}</span></div>
        <div class="s-gauge"><i style="width:${w}%;background:${col}"></i><span class="bank" style="left:${100 / 1.3}%"></span></div>
        <div class="m">น้ำในลำน้ำ ${nf(pct)}% ของตลิ่ง (ที่${r.gauge_name}) · ${tw}${more ? "<br>" + more : ""}</div></div>`;
    }).join("");

    // 5) ควรทำอย่างไร
    const myC = S.home != null && S.home >= 0 ? Math.max(st.c[S.home], st.m72[S.home]) : 0;
    const act = Math.max(lv, myC >= 2 ? 2 : myC);
    const todo = act === 0 ? ["ติดตามข่าวจากอำเภอ ผู้ใหญ่บ้าน และหน้านี้ (ข้อมูลใหม่ทุกชั่วโมง)", "จดเบอร์โทรฉุกเฉินไว้ข้างล่าง", "ถ้าบ้านอยู่ที่ลุ่ม เตรียมที่วางของบนที่สูงไว้ก่อน"] :
      ["ยกเอกสารสำคัญ ยา ของมีค่า และปลั๊กไฟ ขึ้นที่สูง", "ย้ายรถ สัตว์เลี้ยง และเครื่องมือเกษตรไปไว้ที่สูง", "เตรียมน้ำดื่ม อาหารแห้ง ไฟฉาย และแบตสำรอง ให้พอ 3 วัน",
        "ถ้าน้ำเข้าบ้าน ให้ปิดสะพานไฟ อย่าแตะปลั๊กหรือสายไฟที่เปียก", "อย่าเดินหรือขับรถผ่านน้ำไหลแรง — น้ำแค่ระดับเข่าก็พัดคนล้มได้", "ดูแลเด็ก ผู้สูงอายุ และคนป่วย ระวังงูและสัตว์มีพิษ"];

    box.innerHTML = hero +
      `<div class="s-card"><h2><span class="num">1</span>บ้านของฉัน</h2>${homeHtml}</div>` +
      `<div class="s-card"><h2><span class="num">2</span>น้ำท่วมรายอำเภอ</h2>${ampHtml}<p class="s-hint">แตะชื่ออำเภอเพื่อดูบนแผนที่</p></div>` +
      `<div class="s-card"><h2><span class="num">3</span>แม่น้ำสายหลัก</h2>${rv || `<p class="s-small">ยังไม่มีข้อมูลแม่น้ำ</p>`}</div>` +
      `<div class="s-card"><h2><span class="num">4</span>ฝน</h2><p style="margin:0">24 ชม. ที่ผ่านมา: <b>${rainWord(p24)}</b> (เฉลี่ย ${nf(p24)} มม.)<br>3 วันข้างหน้า: <b>${rainWord(f72)}</b> (เฉลี่ย ${nf(f72)} มม.)</p></div>` +
      `<div class="s-card"><h2><span class="num">5</span>ควรทำอย่างไร</h2><ul class="s-todo">${todo.map(x => `<li>${x}</li>`).join("")}</ul>
        <div class="s-tel"><a href="tel:1784"><b>1784</b>ปภ. สายด่วนนิรภัย</a><a href="tel:1669"><b>1669</b>เจ็บป่วยฉุกเฉิน</a><a href="tel:191"><b>191</b>เหตุด่วนเหตุร้าย</a><a href="tel:1460"><b>1460</b>กรมชลประทาน</a></div></div>` +
      histSimpleHtml() +
      `<div class="s-card"><h2>ความลึกบนแผนที่ อ่านอย่างไร</h2><div class="s-depthkey">${[1, 2, 3, 4].map(c => `<div>${person(c)}${DEPTH_WORD[c].replace("ระดับ", "")}<br><span class="note">${DEPTH_CM[c]}</span></div>`).join("")}</div>
        <p class="s-small" style="margin:8px 0 0">สีฟ้าอ่อน = น้ำตื้น · สีน้ำเงินเข้ม = น้ำลึก ; ใช้ปุ่มด้านล่างแผนที่เพื่อดู <b>พรุ่งนี้ / มะรืนนี้ / อีก 3 วัน</b></p></div>` +
      `<p class="s-small">ตัวเลขทั้งหมดเป็น <b>การประมาณจากคอมพิวเตอร์</b> โดยใช้ฝนจริง ระดับน้ำจริงจากสถานี และแผนที่ความสูงพื้นดิน ใช้ดูแนวโน้มเพื่อเตรียมตัว ควรฟังประกาศจากอำเภอ ผู้ใหญ่บ้าน และ ปภ. ประกอบเสมอ · <a href="#" id="toExpert">ดูข้อมูลละเอียด</a></p>`;

    $$("#simple .s-amp").forEach(el => el.onclick = () => { zoomAmphoe(el.dataset.a); if (innerWidth <= 760) $("#panel").classList.add("collapsed"); setTimeout(() => map.invalidateSize(), 250); });
    const on = (id, f) => { const e = $("#" + id); if (e) e.onclick = f; };
    on("homeLoc", locateMe); on("homeClr", () => setHome(null));
    on("homeGo", () => { map.setView([p.lat[S.home], p.lon[S.home]], 12); simplePopup(S.home, L.latLng(p.lat[S.home], p.lon[S.home])); });
    on("toTk", e => { e?.preventDefault?.(); setView("expert"); tkShow("s1"); });
    wireHistSimple();
    on("toExpert", e => { e.preventDefault(); setView("expert"); });
  }

  function simpleLegend() {
    if (S.mode === "hist") return histLegend();
    const pic = c => person(c, "pic");
    const row = (c, col, t) => `<div class="row">${pic(c)}<span class="sw" style="background:${col}"></span>${t}</div>`;
    if (S.mode === "dur") return "<b>น้ำจะขังนานแค่ไหน</b>" + ["ไม่ถึง 1 วัน", "ไม่ถึง 1 วัน", "1–3 วัน", "3–7 วัน", "นานกว่า 1 สัปดาห์"].map((t, k) => k === 0 ? "" : `<div class="row"><span class="sw" style="background:${DUR_COL[k + 1]}"></span>${t}</div>`).join("");
    const t = S.frames.t[S.frame], dh = Math.round((t - S.frames.now) / 86400);
    return `<b>ความลึกน้ำ${dh <= 0 ? "ตอนนี้" : dh === 1 ? "พรุ่งนี้" : dh === 2 ? "มะรืนนี้" : "อีก 3 วัน"}</b>` + [1, 2, 3, 4].map(c => row(c, CLS_COL[c], DEPTH_WORD[c].replace("ระดับ", ""))).join("");
  }
  function setSimpleTime(d) {
    $$("#stime button").forEach(b => b.classList.toggle("active", b.dataset.d === String(d)));
    histOverlay(d === "hist");
    if (d === "hist") { S.mode = "hist"; S.frame = S.frames.t.indexOf(S.frames.now); }
    else if (d === "dur") { S.mode = "dur"; S.frame = S.frames.t.indexOf(S.frames.now); }
    else { S.mode = "class"; S.frame = frameAtDay(+d); }
    S.userMoved = +d > 0; $("#mode").value = S.mode; $("#slider").value = S.frame;
    styleHex(); renderLegend(); renderTimelineLabel(); renderSimple();
  }
  $$("#stime button").forEach(b => b.onclick = () => setSimpleTime(b.dataset.d === "dur" || b.dataset.d === "hist" ? b.dataset.d : +b.dataset.d));

  function setView(v, save = true) {
    S.view = v; document.body.classList.toggle("view-simple", v === "simple"); map.closePopup();
    $$(".viewsw button").forEach(b => b.classList.toggle("active", b.dataset.view === v));
    if (save) store.set("nsf_view", v);
    if (S.stLayer) { if (v === "simple") { map.removeLayer(S.stLayer); if (S.rainLayer) map.removeLayer(S.rainLayer); } else S.stLayer.addTo(map); }
    if (v === "simple" && S.frames) setSimpleTime(S.mode === "dur" || S.mode === "hist" ? S.mode : 0);
    else if (S.frames) { styleHex(); renderLegend(); }
    setTimeout(() => map.invalidateSize(), 50);
  }
  $$(".viewsw button").forEach(b => b.onclick = () => setView(b.dataset.view));
  {
    const q = new URLSearchParams(location.search).get("view");
    S.view = q === "expert" || q === "simple" ? q : (store.get("nsf_view") || "simple");
    const h = store.get("nsf_home"); S.home = h != null && h !== "" ? +h : null;
    setView(S.view, false);
  }

  // ---------------------------------------------------------------- UI wiring
  $("#mode").onchange = e => setMapMode(e.target.value);
  $("#slider").oninput = e => { S.frame = +e.target.value; S.userMoved = true; styleHex(); renderTimelineLabel(); };
  $("#nowBtn").onclick = () => { S.frame = S.frames.t.indexOf(S.frames.now); $("#slider").value = S.frame; S.userMoved = false; styleHex(); renderTimelineLabel(); };
  $("#play").onclick = () => {
    if (S.playing) { clearInterval(S.playing); S.playing = null; $("#play").textContent = "▶"; return; }
    $("#play").textContent = "❚❚"; S.userMoved = true;
    S.playing = setInterval(() => { S.frame = (S.frame + 1) % S.frames.t.length; $("#slider").value = S.frame; styleHex(); renderTimelineLabel(); }, 450);
  };
  $$(".tabs button").forEach(b => b.onclick = () => {
    $$(".tabs button").forEach(x => x.classList.toggle("active", x === b));
    $$(".pane").forEach(p => p.classList.toggle("active", p.dataset.pane === b.dataset.tab));
    if (b.dataset.tab === "windy") setWindy(S.windyOv || "rain");
    if (b.dataset.tab === "upstream") showUpstreamMap();
    if (b.dataset.tab === "hot") showHotMap();
    if (b.dataset.tab === "thatako") showThatako();
    if (b.dataset.tab === "hist") setMapMode("hist", true);
  });
  function setWindy(ov) {
    S.windyOv = ov;
    $$("#windySeg button").forEach(x => x.classList.toggle("active", x.dataset.ov === ov));
    $("#windy").src = `https://embed.windy.com/embed.html?type=map&location=coordinates&metricRain=mm&metricTemp=%C2%B0C&metricWind=km%2Fh&zoom=8&overlay=${ov}&product=ecmwf&level=surface&lat=15.7&lon=100.1&detailLat=15.7&detailLon=100.1&marker=true&message=true`;
  }
  $$("#windySeg button").forEach(b => b.onclick = () => setWindy(b.dataset.ov));
  $("#panelToggle").onclick = () => { $("#panel").classList.toggle("collapsed"); setTimeout(() => map.invalidateSize(), 250); };
  map.on("overlayadd overlayremove", renderLegend);
  map.on("popupopen", () => document.body.classList.add("popup-open")).on("popupclose", () => document.body.classList.remove("popup-open"));

  (async () => {
    try {
      await loadStatic();
      if (S.home != null && !(S.home >= 0 && S.home < S.params.n)) S.home = null;
      await loadLive(true);
      if (S.home != null) setHome(S.home);
      if (S.view === "simple") setSimpleTime(0);
    }
    catch (e) { $("#updated").textContent = "โหลดข้อมูลไม่สำเร็จ: " + e.message; console.error(e); }
    setInterval(() => loadLive(false).catch(console.warn), REFRESH_MS);
  })();
})();
