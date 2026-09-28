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
  const WL_COL = { 1: "#b45309", 2: "#eab308", 3: "#16a34a", 4: "#2563eb", 5: "#dc2626" };
  const WL_LBL = { 1: "น้อยวิกฤต", 2: "น้อย", 3: "ปกติ", 4: "มาก", 5: "ล้นตลิ่ง" };
  const SRC_LBL = ["–", "น้ำฝนท่วมขัง", "น้ำล้นตลิ่ง", "ฝน + ล้นตลิ่ง"];

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const fmtT = (unix, withDay = true) => new Date(unix * 1000).toLocaleString("th-TH",
    { timeZone: "Asia/Bangkok", ...(withDay ? { day: "numeric", month: "short" } : {}), hour: "2-digit", minute: "2-digit" });
  const nf = (v, d = 0) => (v == null || v < 0) ? "–" : Number(v).toLocaleString("th-TH", { maximumFractionDigits: d, minimumFractionDigits: d });
  const binCol = (v, br, cols) => { let i = 0; while (i < br.length && v >= br[i]) i++; return cols[i]; };
  const durTxt = h => h < 0 ? "–" : h >= 999 ? "> 14 วัน" : h >= 48 ? `${Math.round(h / 24)} วัน` : `${h} ชม.`;

  const S = { params: null, status: null, frames: null, meta: null, stations: null, districts: null, series: null,
    hexLayer: null, frame: 0, mode: "class", playing: null, ver: null };

  // ---------------------------------------------------------------- map
  const map = L.map("map", { zoomControl: true, preferCanvas: true, minZoom: 7 }).setView([15.72, 100.0], 9);
  const base = {
    "แผนที่ (CARTO)": L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      { attribution: "© OpenStreetMap © CARTO", subdomains: "abcd", maxZoom: 19 }),
    "OpenStreetMap": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap", maxZoom: 19 }),
    "ภาพถ่ายดาวเทียม (Esri)": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Esri, Maxar, Earthstar Geographics", maxZoom: 19 }),
  };
  base["แผนที่ (CARTO)"].addTo(map);
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
    S.hexLayer = L.geoJSON(hex, { renderer, style: () => ({ weight: 0, fillOpacity: 0 }), onEachFeature: (f, l) => l.on("click", e => hexPopup(f.properties.i, e.latlng)) });
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
    Object.assign(S, { meta, status, frames, stations, districts, series });
    const nowIdx = frames.t.indexOf(frames.now);
    const sl = $("#slider"); sl.max = frames.t.length - 1;
    if (S.frame === 0 || force || !S.userMoved) S.frame = nowIdx;
    sl.value = S.frame;
    $("#tlStart").textContent = fmtT(frames.t[0]); $("#tlEnd").textContent = fmtT(frames.t[frames.t.length - 1]);
    renderAll();
    if (meta.sources?.gistda?.ok) loadGistda(v);
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
    renderDistricts(); renderStations(); renderRain(); renderAbout();
  }

  function hexStyleFn() {
    const st = S.status, m = S.mode;
    const frameCls = S.frames.c[S.frame];
    const isNow = S.frames.t[S.frame] === S.frames.now;
    if (m === "class") return i => { const c = +frameCls[i]; return { fillColor: CLS_COL[c], fillOpacity: c ? .78 : 0 }; };
    if (m === "m72") return i => { const c = st.m72[i]; return { fillColor: CLS_COL[c], fillOpacity: c ? .78 : 0 }; };
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
    const m = S.mode; let h = "";
    const row = (c, t) => `<div class="row"><span class="sw" style="background:${c}"></span>${t}</div>`;
    if (m === "class" || m === "m72") { h = `<b>${m === "class" ? "ความลึกน้ำท่วมขัง (ประมาณ)" : "ความลึกสูงสุดใน 72 ชม."}</b>` + CLS_LBL.slice(1).map((t, k) => row(CLS_COL[k + 1], t)).join(""); }
    else if (m === "dur") { h = "<b>ระยะเวลาท่วมรวม (ผ่านมา+คาดการณ์)</b>" + ["1–12 ชม.", "12–24 ชม.", "1–3 วัน", "3–7 วัน", "> 7 วัน"].map((t, k) => row(DUR_COL[k + 1], t)).join(""); }
    else { h = `<b>${{ p24: "ฝน 24 ชม.", p7d: "ฝน 7 วัน", f72: "ฝนพยากรณ์ 72 ชม." }[m]} (มม.)</b>` + ["1–10", "10–35", "35–90", "90–150", "> 150"].map((t, k) => row(RAIN_COL[k + 1], t)).join(""); }
    if (S.suscMeta && map.hasLayer(S.susc)) h += "<b style='margin-top:6px'>พื้นที่ลุ่มต่ำ (HAND)</b>" + S.suscMeta.classes.map((t, k) => row(S.suscMeta.colors[k], t)).join("");
    $("#legend").innerHTML = h;
  }

  function renderKpis() {
    const s = S.meta.summary;
    const k = (v, u, l, hot) => `<div class="kpi${hot ? " hot" : ""}"><div class="v">${v}<small>${u}</small></div><div class="l">${l}</div></div>`;
    $("#kpis").innerHTML = k(nf(s.km2_now), "กม²", "ท่วมขัง ≥25 ซม. ตอนนี้", s.km2_now > 0) + k(nf(s.km2_watch), "กม²", "เฝ้าระวัง ≥10 ซม.") +
      k(nf(s.km2_72h), "กม²", "คาดท่วมขังใน 72 ชม.", s.km2_72h > s.km2_now) + k(nf(s.rain24_max), "มม.", "ฝน 24 ชม. สูงสุด (hex)") +
      k(nf(s.fc72_max), "มม.", "ฝนพยากรณ์ 72 ชม. สูงสุด") + k(nf(s.wl_over_bank), "สถานี", "ระดับน้ำล้นตลิ่ง (นครสวรรค์)", s.wl_over_bank > 0);
  }

  function renderTimelineLabel() {
    const t = S.frames.t[S.frame], fc = t > S.frames.now;
    const dh = Math.round((t - S.frames.now) / 3600);
    const el = $("#tlLabel"); el.className = "tl-now" + (fc ? " fc" : "");
    el.textContent = `${fmtT(t)} · ${dh === 0 ? "ตอนนี้" : dh > 0 ? `พยากรณ์ +${dh} ชม.` : `${dh} ชม.`}`;
  }

  function renderDistricts() {
    const tb = $("#distTable tbody"); if (!S.districts) { tb.innerHTML = ""; return; }
    tb.innerHTML = S.districts.map(d => `<tr data-a="${d.amphoe}"><td><span class="sw" style="background:${CLS_COL[d.max_cls_now] === CLS_COL[0] ? "#fff" : CLS_COL[d.max_cls_now]}"></span>${d.amphoe}</td><td>${nf(d.km2_now)}</td><td>${nf(d.km2_72h)}</td><td>${nf(d.rain24, 1)}</td></tr>`).join("");
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
    })).addTo(map);
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
      <span>แนวโน้ม</span><span>${s.trend == null ? "–" : (s.trend > 0 ? "ขึ้น " : s.trend < 0 ? "ลง " : "ทรงตัว ") + nf(Math.abs(s.trend), 2) + " ม."}</span>
      <span>เวลา</span><span>${s.time}</span><span>หน่วยงาน</span><span>${s.agency}</span></div></div>`;
  }

  // ---------------------------------------------------------------- hex popup
  function hexPopup(i, latlng) {
    const st = S.status, p = S.params;
    const c = st.c[i], a = p.amphoe_list[p.amph[i]];
    const fr = +S.frames.c[S.frame][i];
    const html = `<div class="pp"><h4>อ.${a} · hex #${i}</h4>
      <div class="grid">
        <span>สถานะตอนนี้</span><span><span class="sw" style="background:${CLS_COL[c] === CLS_COL[0] ? "#fff" : CLS_COL[c]}"></span><b>${CLS_SHORT[c]}</b></span>
        <span>ความลึกประมาณ</span><span>${nf(st.d[i])} ซม.</span>
        <span>สาเหตุ</span><span>${SRC_LBL[st.s[i]]}</span>
        <span>ท่วมมาแล้ว</span><span>${st.h[i] > 0 ? durTxt(st.h[i]) : "–"}</span>
        <span>คาดว่าจะลดลงใน</span><span>${durTxt(st.r[i])}</span>
        ${S.frames.t[S.frame] !== S.frames.now ? `<span>ณ เวลาที่เลือก</span><span>${CLS_SHORT[fr]}</span>` : ""}
      </div><hr><div class="grid">
        <span>ฝน 24 ชม. / 72 ชม.</span><span>${nf(st.p24[i])} / ${nf(st.p72[i])} มม.</span>
        <span>ฝน 7 วัน</span><span>${nf(st.p7d[i])} มม.</span>
        <span>พยากรณ์ 24 / 72 ชม.</span><span>${nf(st.f24[i])} / ${nf(st.f72[i])} มม.</span>
        <span>สูงสุดใน 72 ชม.</span><span>${CLS_SHORT[st.m72[i]]} (${nf(st.dm72[i])} ซม.)</span>
      </div><hr><div class="grid">
        <span>HAND เฉลี่ย / P10</span><span>${nf(p.hand[i], 1)} / ${nf(p.hand_p10[i], 1)} ม.</span>
        <span>สัดส่วนที่ลุ่ม (HAND&lt;2ม.)</span><span>${nf(p.f_low[i] * 100)}%</span>
        <span>Curve Number</span><span>${nf(p.cn[i])}</span>
        <span>ความลาดชัน</span><span>${nf(p.slope[i], 1)}%</span>
        <span>นา / เมือง</span><span>${nf(p.f_crop[i] * 100)}% / ${nf(p.f_built[i] * 100)}%</span>
      </div></div>`;
    L.popup({ maxWidth: 320 }).setLatLng(latlng).setContent(html).openOn(map);
  }

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
        <li>${ok("gistda")} GISTDA น้ำท่วมจากดาวเทียม ${src.gistda?.ok ? src.gistda.features + " แปลง" : "(" + (src.gistda?.error || "ปิด") + ")"}</li></ul>
      <p class="note">โมเดล: ${m.model} · ใช้เวลา ${m.runtime_s} วินาที</p>
      <h4>ขั้นตอนวิเคราะห์</h4>
      <p><b>ชั้นข้อมูลคงที่ (ArcGIS Pro)</b> — Copernicus DEM 30 ม. → Fill, Flow Direction (D8), Flow Accumulation, HAND (Flow Distance แนวดิ่งถึงลำน้ำ), ความจุแอ่ง (Fill − DEM), ความลาดชัน; ESA WorldCover → Curve Number (HSG C/D) และสัดส่วนนา/เมือง; สรุปลง hex 1 กม² พร้อม hex ท้ายน้ำสำหรับ routing</p>
      <p><b>ทุก 1 ชั่วโมง (GitHub Actions)</b> — ฝนรายชั่วโมงย้อนหลัง 30 วัน + พยากรณ์ 72 ชม. จาก Open-Meteo ปรับแก้ 24 ชม. ล่าสุดด้วยสถานีวัดฝน ThaiWater → SCS-CN (AMC จากฝน 5 วัน) → กักเก็บในแอ่ง/คันนา → ระบายแบบ linear reservoir ตามความลาดชันไปยัง hex ท้ายน้ำ → ความลึกน้ำขัง; น้ำล้นตลิ่งคำนวณจากระดับน้ำสถานีเทียบ HAND ของแม่น้ำสายหลัก และลดอัตราระบายเมื่อระดับน้ำใกล้ตลิ่ง</p>
      <p><b>ระยะเวลาท่วม</b> — จำนวนชั่วโมงที่ความลึก ≥ 10 ซม. ต่อเนื่องถึงปัจจุบัน และเวลาที่คาดว่าจะลดต่ำกว่า 10 ซม. จากการจำลองต่อด้วยฝนพยากรณ์ (เกิน 72 ชม. ใช้อัตราการลดช่วงท้าย / น้ำล้นตลิ่งใช้แนวโน้มระดับน้ำ)</p>
      <h4>ข้อจำกัด</h4><ul>
        <li>เป็นแบบจำลองเชิงคัดกรอง (screening) ไม่ใช่แบบจำลองชลศาสตร์ 2 มิติ ความลึกเป็นค่าประมาณเฉลี่ยในส่วนที่ลุ่มของ hex</li>
        <li>DEM เป็น DSM (รวมอาคาร/ต้นไม้) ไม่รวมคันกั้นน้ำ ประตูระบายน้ำ และระบบสูบน้ำในเขตเมือง</li>
        <li>ฝนย้อนหลังเป็นค่าจากแบบจำลองอากาศ ปรับแก้เฉพาะ 24 ชม. ล่าสุด ควรสอบเทียบกับพื้นที่ท่วมจริง (GISTDA) ก่อนใช้ตัดสินใจ</li></ul>
      <h4>แหล่งข้อมูล</h4><ul>
        <li>Open-Meteo (CC BY 4.0) · ThaiWater / สสน. · GISTDA · Windy.com</li>
        <li>Copernicus DEM GLO-30 (© DLR/Airbus, ESA) · ESA WorldCover 2021 (CC BY 4.0) · geoBoundaries</li></ul>`;
  }

  // ---------------------------------------------------------------- UI wiring
  $("#mode").onchange = e => { S.mode = e.target.value; styleHex(); renderLegend(); };
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
  });
  function setWindy(ov) {
    S.windyOv = ov;
    $$("#windySeg button").forEach(x => x.classList.toggle("active", x.dataset.ov === ov));
    $("#windy").src = `https://embed.windy.com/embed.html?type=map&location=coordinates&metricRain=mm&metricTemp=%C2%B0C&metricWind=km%2Fh&zoom=8&overlay=${ov}&product=ecmwf&level=surface&lat=15.7&lon=100.1&detailLat=15.7&detailLon=100.1&marker=true&message=true`;
  }
  $$("#windySeg button").forEach(b => b.onclick = () => setWindy(b.dataset.ov));
  $("#panelToggle").onclick = () => { $("#panel").classList.toggle("collapsed"); setTimeout(() => map.invalidateSize(), 250); };
  map.on("overlayadd overlayremove", renderLegend);

  (async () => {
    try { await loadStatic(); await loadLive(true); }
    catch (e) { $("#updated").textContent = "โหลดข้อมูลไม่สำเร็จ: " + e.message; console.error(e); }
    setInterval(() => loadLive(false).catch(console.warn), REFRESH_MS);
  })();
})();
