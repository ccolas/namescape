// Namescape — static frontend (no backend; everything in-browser)
//
// On boot we fetch data/config.json (tag tree, palettes, city index). On city
// selection we fetch data/<city>.json (districts geojson + pre-stemmed POI
// records). Running a query is pure in-memory: filter elements by the
// selected tag specs, apply the keyword/matcher, aggregate per district.

// ---------------- state ----------------

let map, districtsLayer;
let paletteMap = {};
let tagCategories = [];
let cities = [];

let currentCityId = null;
let currentCityBundle = null;   // { meta, districts, elements }
let currentPalette = "Viridis";
let currentScale = "auto";
let lastResult = null;
let sortKey = "fraction";
let sortDir = "desc";
let selectedDistrict = null;
let layerByName = {};

const MODE_HINTS = {
  substring: "Diacritic-insensitive substring match on the full name. Fastest, loosest.",
  root:      "Loose root matching against pre-computed Snowball stems. Catches suffix variants in inflected languages (kitap → kitapçı, kitaplar, kitabı).",
  regex:     "User regex, case-insensitive, applied to the original name (with diacritics).",
};

// ---------------- boot ----------------

async function init() {
  setStatus("Loading site config…", "working");
  let cfg;
  try {
    cfg = await fetch("data/config.json").then(r => {
      if (!r.ok) throw new Error(`data/config.json: HTTP ${r.status}`);
      return r.json();
    });
  } catch (e) {
    setStatus(`Failed to load config: ${e.message}. Did you run \`python -m static.build\`?`, "error");
    return;
  }
  tagCategories = cfg.tag_categories;
  paletteMap = cfg.palettes;
  cities = cfg.cities;

  renderPalettes(Object.keys(paletteMap));
  renderTags(tagCategories);
  renderCities(cities);
  setModeHint();
  setupMap();

  document.getElementById("city").addEventListener("change", e => loadCity(e.target.value));
  document.getElementById("palette").addEventListener("change", e => {
    currentPalette = e.target.value;
    applyStyling(); renderLegend(); renderTable();
  });
  document.querySelectorAll('input[name="scale"]').forEach(el => {
    el.addEventListener("change", () => {
      currentScale = el.value;
      applyStyling(); renderLegend(); renderTable();
    });
  });
  document.querySelectorAll('input[name="mode"]').forEach(el => el.addEventListener("change", setModeHint));
  document.getElementById("run").addEventListener("click", runQuery);
  document.getElementById("select-all").addEventListener("click", () => toggleAll(true));
  document.getElementById("deselect-all").addEventListener("click", () => toggleAll(false));
  document.getElementById("dl-summary").addEventListener("click", downloadSummaryCSV);
  document.getElementById("dl-matches").addEventListener("click", downloadMatchesCSV);
  document.querySelectorAll("#district-table th[data-sort]").forEach(th => {
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (sortKey === k) sortDir = (sortDir === "desc" ? "asc" : "desc");
      else { sortKey = k; sortDir = (k === "name" ? "asc" : "desc"); }
      renderTable();
    });
  });

  if (cities.length) {
    const first = cities[0].id;
    document.getElementById("city").value = first;
    await loadCity(first);
  }
}

function setModeHint() {
  const m = document.querySelector('input[name="mode"]:checked').value;
  document.getElementById("mode-hint").textContent = MODE_HINTS[m] || "";
}

// ---------------- city selection ----------------

function renderCities(cs) {
  const sel = document.getElementById("city");
  sel.innerHTML = "";
  for (const c of cs) {
    const o = document.createElement("option");
    o.value = c.id;
    o.textContent = `${c.label}, ${c.country}`;
    sel.appendChild(o);
  }
}

async function loadCity(cityId) {
  currentCityId = cityId;
  const meta = cities.find(c => c.id === cityId);
  document.getElementById("city-meta").textContent =
    `${meta.n_districts} ${meta.subdivision}${meta.n_districts > 1 ? "s" : ""} · ${meta.n_elements.toLocaleString()} POIs · stemmer: ${meta.stemmer_language}`;

  showLoadingOverlay(`Loading ${meta.label}… (~${estimateMB(meta.n_elements)} MB)`);
  try {
    const bundle = await fetch(`data/${cityId}.json`).then(r => {
      if (!r.ok) throw new Error(`data/${cityId}.json: HTTP ${r.status}`);
      return r.json();
    });
    currentCityBundle = bundle;
  } catch (e) {
    hideLoadingOverlay();
    setStatus(`Failed to load city: ${e.message}`, "error");
    return;
  }

  // Reset result state
  lastResult = null;
  selectedDistrict = null;
  document.getElementById("results-card").classList.add("hidden");
  document.getElementById("district-panel").classList.add("hidden");

  // Re-center map + draw district layer
  map.setView(currentCityBundle.meta.map_center, currentCityBundle.meta.map_zoom);
  renderDistrictLayer(currentCityBundle.districts);

  hideLoadingOverlay();
  setStatus(`Ready. ${currentCityBundle.elements.length.toLocaleString()} locations in ${currentCityBundle.districts.features.length} ${currentCityBundle.meta.subdivision}s.`);
}

function estimateMB(nElements) {
  // Rough — used only for UX message.
  return Math.max(1, Math.round(nElements * 0.0004));
}

// ---------------- palettes / tags / map ----------------

function renderPalettes(names) {
  const sel = document.getElementById("palette");
  sel.innerHTML = "";
  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    if (name === currentPalette) opt.selected = true;
    sel.appendChild(opt);
  }
}

function renderTags(cats) {
  const container = document.getElementById("tag-tree");
  container.innerHTML = "";
  for (const cat of cats) {
    const catDiv = document.createElement("div");
    catDiv.className = "category";
    const header = document.createElement("div");
    header.className = "cat-header";
    const chevron = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chevron.setAttribute("viewBox", "0 0 10 10");
    chevron.classList.add("cat-chevron");
    chevron.innerHTML = '<path fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" d="M2 3.5l3 3 3-3"/>';
    header.appendChild(chevron);
    const catCb = document.createElement("input");
    catCb.type = "checkbox"; catCb.className = "cat-cb";
    catCb.addEventListener("click", e => e.stopPropagation());
    header.appendChild(catCb);
    const lbl = document.createElement("span"); lbl.className = "cat-label"; lbl.textContent = cat.label;
    header.appendChild(lbl);
    const count = document.createElement("span"); count.className = "cat-count"; count.textContent = cat.tags.length;
    header.appendChild(count);
    catDiv.appendChild(header);

    const list = document.createElement("div"); list.className = "tag-list";
    const boxes = [];
    let masterBox = null;
    for (const tag of cat.tags) {
      const row = document.createElement("label");
      row.className = "tag-row";
      if (tag.value === "*") row.classList.add("master");
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.className = "tag-cb";
      cb.dataset.tag = JSON.stringify(tag);
      cb.dataset.specId = specId(tag);
      row.appendChild(cb);
      const sp = document.createElement("span"); sp.textContent = tag.label; row.appendChild(sp);
      list.appendChild(row);
      boxes.push(cb);
      if (tag.value === "*") masterBox = cb;
    }
    catDiv.appendChild(list);
    container.appendChild(catDiv);

    const updateHeader = () => {
      const n = boxes.filter(b => b.checked).length;
      catCb.checked = n === boxes.length;
      catCb.indeterminate = n > 0 && n < boxes.length;
      count.textContent = n > 0 ? `${n}/${boxes.length}` : boxes.length;
      count.classList.toggle("active", n > 0);
    };
    const setAll = (on) => { for (const cb of boxes) cb.checked = on; updateHeader(); };

    catCb.addEventListener("change", () => setAll(catCb.checked));
    header.addEventListener("click", (e) => { if (e.target === catCb) return; catDiv.classList.toggle("collapsed"); });
    if (masterBox) masterBox.addEventListener("change", () => setAll(masterBox.checked));
    for (const cb of boxes) {
      if (cb === masterBox) continue;
      cb.addEventListener("change", () => {
        if (masterBox && masterBox.checked && !cb.checked) masterBox.checked = false;
        if (masterBox && !masterBox.checked && boxes.every(b => b.checked || b === masterBox)) masterBox.checked = true;
        updateHeader();
      });
    }
  }
}

function specId(tag) {
  let sid = `${tag.key}=${tag.value}`;
  if (tag.filter_key) sid += `|${tag.filter_key}=${tag.filter_value}`;
  return sid;
}

function toggleAll(on) {
  document.querySelectorAll("#tag-tree .tag-cb").forEach(cb => { cb.checked = on; });
  document.querySelectorAll("#tag-tree .cat-cb").forEach(cb => { cb.checked = on; cb.indeterminate = false; });
  document.querySelectorAll("#tag-tree .cat-count").forEach(c => {
    const list = c.closest(".category").querySelectorAll(".tag-cb");
    c.textContent = on ? `${list.length}/${list.length}` : list.length;
    c.classList.toggle("active", on);
  });
}

function setupMap() {
  map = L.map("map", { preferCanvas: true }).setView([41.05, 29.0], 10);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: 19,
    subdomains: "abcd",
  }).addTo(map);
}

function renderDistrictLayer(geo) {
  if (districtsLayer) map.removeLayer(districtsLayer);
  layerByName = {};
  districtsLayer = L.geoJSON(geo, {
    style: () => ({ color: "#8a92a5", weight: 1, fillColor: "#d9dde7", fillOpacity: 0.5, dashArray: "2,3" }),
    onEachFeature: (feat, layer) => {
      const name = feat.properties.name;
      layerByName[name] = layer;
      layer.bindTooltip(name, { sticky: true, className: "district-tooltip" });
      layer.on("click", () => {
        selectedDistrict = name;
        showDistrictPanel(name);
        renderTable();
        applyStyling();
      });
      layer.on("mouseover", () => {
        layer.setStyle({ weight: 2.5, color: "#1a1e2c" });
        layer.bringToFront();
      });
      layer.on("mouseout", () => { applyStyling(); });
    },
  }).addTo(map);
  try { map.fitBounds(districtsLayer.getBounds(), { padding: [20, 20] }); } catch {}
}

// ---------------- color ----------------

function hexToRgb(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function rgbToHex([r,g,b]) { return "#" + ((r<<16)|(g<<8)|b).toString(16).padStart(6, "0"); }
function lerpHex(a, b, t) { const ra = hexToRgb(a), rb = hexToRgb(b); return rgbToHex(ra.map((v,i) => Math.round(v + (rb[i]-v)*t))); }
function paletteStops() { return paletteMap[currentPalette] || paletteMap["Viridis"]; }
function sampleStops(stops, t) {
  const f = Math.max(0, Math.min(1, t));
  const x = f * (stops.length - 1);
  const i = Math.floor(x), frac = x - i;
  if (i >= stops.length - 1) return stops[stops.length - 1];
  return lerpHex(stops[i], stops[i+1], frac);
}
function scaleMax() {
  if (currentScale === "absolute") return 1.0;
  if (!lastResult) return 1.0;
  let mx = 0;
  for (const s of Object.values(lastResult.per_district)) {
    if (s.count > 0 && s.fraction > mx) mx = s.fraction;
  }
  return mx > 0 ? mx : 1.0;
}
function colorFor(fraction) {
  const mx = scaleMax();
  return sampleStops(paletteStops(), mx > 0 ? fraction / mx : 0);
}

// ---------------- matching (in-browser) ----------------

const TR_MAP = {
  "ş":"s","Ş":"s","ı":"i","İ":"i","ğ":"g","Ğ":"g",
  "ç":"c","Ç":"c","ö":"o","Ö":"o","ü":"u","Ü":"u",
};
function normalize(text) {
  if (!text) return "";
  let t = "";
  for (const ch of text) t += (TR_MAP[ch] ?? ch);
  t = t.toLowerCase();
  return t.normalize("NFKD").replace(/[̀-ͯ]/g, "");
}

function sharedPrefixLen(a, b) {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a.charCodeAt(i) === b.charCodeAt(i)) i++;
  return i;
}

function buildMatcher(keywords, mode) {
  const kws = (keywords || []).map(k => k.trim()).filter(Boolean);
  if (!kws.length) return () => false;

  if (mode === "substring") {
    const nk = kws.map(normalize);
    return (el) => {
      const n = el._nn || (el._nn = normalize(el.n));
      return nk.some(k => n.indexOf(k) !== -1);
    };
  }

  if (mode === "root") {
    const nk = kws.map(normalize);
    return (el) => {
      const stems = el.stems || [];
      for (const k of nk) {
        if (!k) continue;
        for (const s of stems) {
          // 1) stem contains keyword  (kitap ⊂ kitapçı)
          if (s.indexOf(k) !== -1) return true;
          // 2) keyword contains stem  (user types kitapevi, stem is kitap)
          if (s.length >= 4 && k.indexOf(s) !== -1) return true;
          // 3) keyword and stem differ only in the final character
          //    (covers Turkish kitap/kitab consonant mutation); requires
          //    both to be >= 5 chars and share all but the last char.
          const minLen = Math.min(k.length, s.length);
          if (minLen >= 5 && sharedPrefixLen(k, s) >= minLen - 1) return true;
        }
      }
      return false;
    };
  }

  if (mode === "regex") {
    const regs = [];
    for (const k of kws) { try { regs.push(new RegExp(k, "i")); } catch {} }
    return (el) => regs.some(r => r.test(el.n));
  }
  return () => false;
}

// ---------------- query ----------------

function selectedSpecIds() {
  return new Set(
    [...document.querySelectorAll("#tag-tree .tag-cb:checked")].map(cb => cb.dataset.specId)
  );
}

function parseKeywords() {
  return document.getElementById("keywords").value
    .split(/[\n,]/).map(s => s.trim()).filter(Boolean);
}

function setStatus(text, cls = "") {
  const el = document.getElementById("status");
  el.textContent = text;
  el.className = "status " + cls;
}

function runQuery() {
  if (!currentCityBundle) {
    setStatus("Pick a city first.", "error");
    return;
  }
  const specSet = selectedSpecIds();
  if (!specSet.size) {
    setStatus("Pick at least one location type.", "error");
    return;
  }
  const kws = parseKeywords();
  const mode = document.querySelector('input[name="mode"]:checked').value;

  const t0 = performance.now();
  const matcher = buildMatcher(kws, mode);

  // init per-district stats
  const per = {};
  for (const f of currentCityBundle.districts.features) {
    per[f.properties.name] = { count: 0, matches: 0, examples: [], match_examples: [] };
  }

  for (const el of currentCityBundle.elements) {
    if (!specSet.has(el.s)) continue;
    const stats = per[el.d];
    if (!stats) continue;
    stats.count++;
    if (stats.examples.length < 10) stats.examples.push(el.n);
    if (matcher(el)) {
      stats.matches++;
      stats.match_examples.push(el);
    }
  }

  let totalElements = 0, totalMatches = 0, nonEmpty = 0;
  for (const s of Object.values(per)) {
    s.fraction = s.count > 0 ? s.matches / s.count : 0;
    totalElements += s.count;
    totalMatches += s.matches;
    if (s.count > 0) nonEmpty++;
  }

  lastResult = { per_district: per, total_elements: totalElements, keywords: kws, mode };
  const dt = ((performance.now() - t0) / 1000).toFixed(2);
  setStatus(`Done in ${dt}s.`);

  renderSummaryStats({
    elements: totalElements,
    matches: totalMatches,
    nonEmpty,
    total: Object.keys(per).length,
  });
  document.getElementById("results-card").classList.remove("hidden");
  applyStyling();
  renderLegend();
  renderTable();
}

function renderSummaryStats(s) {
  const overall = s.elements > 0 ? ((s.matches / s.elements) * 100).toFixed(1) : "0.0";
  document.getElementById("summary-stats").innerHTML = `
    <div class="stat-cell"><span class="stat-value">${s.elements.toLocaleString()}</span><span class="stat-label">locations</span></div>
    <div class="stat-cell"><span class="stat-value">${s.matches.toLocaleString()}</span><span class="stat-label">matches</span></div>
    <div class="stat-cell"><span class="stat-value">${overall}%</span><span class="stat-label">overall</span></div>
  `;
}

// ---------------- styling / legend / table ----------------

function applyStyling() {
  if (!districtsLayer) return;
  const per = (lastResult && lastResult.per_district) || {};
  districtsLayer.eachLayer(layer => {
    const name = layer.feature.properties.name;
    const s = per[name];
    const active = selectedDistrict === name;
    let style;
    if (!s || s.count === 0) {
      style = {
        color: active ? "#1a1e2c" : "#8a92a5",
        weight: active ? 2.5 : 1,
        fillColor: "#d9dde7",
        fillOpacity: 0.55,
        dashArray: "2,3",
      };
    } else {
      style = {
        color: active ? "#1a1e2c" : "#3a4050",
        weight: active ? 2.5 : 0.8,
        fillColor: colorFor(s.fraction),
        fillOpacity: 0.78,
        dashArray: null,
      };
    }
    layer.setStyle(style);
    const tt = s && s.count > 0
      ? `<b>${name}</b><br/>${s.matches} / ${s.count} = ${(s.fraction * 100).toFixed(1)}%`
      : `<b>${name}</b><br/><span style="opacity:0.7">no locations</span>`;
    layer.unbindTooltip();
    layer.bindTooltip(tt, { sticky: true, className: "district-tooltip" });
  });
}

function renderLegend() {
  const el = document.getElementById("legend");
  const stops = paletteStops();
  const grad = `linear-gradient(to right, ${stops.join(", ")})`;
  const mx = scaleMax();
  const scaleLabel = currentScale === "auto" ? " · auto-scaled" : "";
  el.innerHTML = `
    <div class="legend-title">Fraction matching${scaleLabel}</div>
    <div class="legend-bar" style="background:${grad}"></div>
    <div class="legend-scale">
      <span>0%</span>
      <span>${(mx * 50).toFixed(1)}%</span>
      <span>${(mx * 100).toFixed(1)}%</span>
    </div>
    <div class="legend-nodata">
      <div class="legend-nodata-swatch"></div>
      <span>no locations in district</span>
    </div>
  `;
}

function renderTable() {
  const tbody = document.querySelector("#district-table tbody");
  if (!tbody) return;
  if (!lastResult) { tbody.innerHTML = ""; return; }
  const rows = Object.entries(lastResult.per_district).map(([name, s]) => ({ name, ...s }));
  rows.sort((a, b) => {
    const dir = sortDir === "asc" ? 1 : -1;
    if (sortKey === "name") return a.name.localeCompare(b.name) * dir;
    if (a.count === 0 && b.count > 0) return 1;
    if (b.count === 0 && a.count > 0) return -1;
    return ((a[sortKey] ?? 0) - (b[sortKey] ?? 0)) * dir;
  });
  document.querySelectorAll("#district-table th").forEach(th => {
    th.classList.toggle("sort-active", th.dataset.sort === sortKey);
    th.classList.toggle("asc", th.dataset.sort === sortKey && sortDir === "asc");
  });
  tbody.innerHTML = rows.map(r => {
    const noData = r.count === 0;
    const pct = noData ? "—" : `${(r.fraction * 100).toFixed(1)}%`;
    const color = noData ? "#d9dde7" : colorFor(r.fraction);
    const bar = noData ? 0 : Math.max(2, r.fraction / Math.max(scaleMax(), 1e-9) * 100);
    const activeCls = selectedDistrict === r.name ? " active" : "";
    const ndCls = noData ? " nodata" : "";
    return `<tr class="row${activeCls}${ndCls}" data-name="${esc(r.name)}">
      <td>${esc(r.name)}</td>
      <td class="num">${r.matches}</td>
      <td class="num">${r.count}</td>
      <td class="num"><span class="frac-bar"><span style="width:${bar}%;background:${color}"></span></span>${pct}</td>
    </tr>`;
  }).join("");
  tbody.querySelectorAll("tr").forEach(tr => {
    tr.addEventListener("click", () => {
      const name = tr.dataset.name;
      selectedDistrict = name;
      showDistrictPanel(name);
      const layer = layerByName[name];
      if (layer) { try { map.fitBounds(layer.getBounds(), { padding: [40, 40], maxZoom: 13 }); } catch {} }
      applyStyling();
      renderTable();
    });
  });
}

// ---------------- district panel ----------------

const esc = (x) => (x == null ? "" : String(x).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])));

function osmUrl(m) {
  if (!m || !m.otype || !m.oid) return null;
  const typeMap = { n: "node", w: "way", r: "relation" };
  return `https://www.openstreetmap.org/${typeMap[m.otype] || "node"}/${m.oid}`;
}

function groupByCategory(matches) {
  const map = new Map();
  for (const m of matches) {
    const cat = m.c || "other";
    if (!map.has(cat)) map.set(cat, []);
    map.get(cat).push(m);
  }
  const groups = [...map.entries()].map(([category, items]) => {
    items.sort((a, b) => a.n.localeCompare(b.n));
    return { category, items };
  });
  groups.sort((a, b) => b.items.length - a.items.length || a.category.localeCompare(b.category));
  return groups;
}

function matchItemHtml(m) {
  const url = osmUrl(m);
  const payload = encodeURIComponent(JSON.stringify(m));
  const nameHtml = url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(m.n)}</a>`
    : `<span>${esc(m.n)}</span>`;
  return `<li class="match-item" data-match="${payload}">${nameHtml}</li>`;
}

let _tooltipEl = null;
function tooltipEl() {
  if (_tooltipEl) return _tooltipEl;
  _tooltipEl = document.createElement("div");
  _tooltipEl.className = "match-tooltip hidden";
  document.body.appendChild(_tooltipEl);
  return _tooltipEl;
}
function hideTooltip() { tooltipEl().classList.add("hidden"); }
function renderTooltip(m, x, y) {
  const el = tooltipEl();
  const rows = [];
  rows.push(`<div class="tt-name">${esc(m.n)}</div>`);
  rows.push(`<div class="tt-cat">${esc(m.c || "")}</div>`);
  if (m.a) rows.push(`<div class="tt-row"><span class="tt-key">address</span><span>${esc(m.a)}</span></div>`);
  if (m.w) {
    const href = m.w.match(/^https?:\/\//) ? m.w : "https://" + m.w;
    rows.push(`<div class="tt-row"><span class="tt-key">website</span><a href="${esc(href)}" target="_blank" rel="noopener">${esc(m.w)}</a></div>`);
  }
  if (m.ig) {
    const handle = m.ig.replace(/^@/, "");
    const href = handle.match(/^https?:\/\//) ? handle : `https://instagram.com/${handle.replace(/^https?:\/\/(www\.)?instagram\.com\//, "")}`;
    rows.push(`<div class="tt-row"><span class="tt-key">instagram</span><a href="${esc(href)}" target="_blank" rel="noopener">${esc(m.ig)}</a></div>`);
  }
  if (m.ot) rows.push(`<div class="tt-row"><span class="tt-key">operator:type</span><span>${esc(m.ot)}</span></div>`);
  if (m.on && !m.ot) rows.push(`<div class="tt-row"><span class="tt-key">operator</span><span>${esc(m.on)}</span></div>`);
  if (m.old) rows.push(`<div class="tt-row"><span class="tt-key">formerly</span><span>${esc(m.old)}</span></div>`);
  if (m.ts) rows.push(`<div class="tt-meta">last edited ${esc(m.ts)}</div>`);
  const url = osmUrl(m);
  if (url) rows.push(`<div class="tt-meta"><a href="${esc(url)}" target="_blank" rel="noopener">open on OSM ↗</a></div>`);
  el.innerHTML = rows.join("");
  el.classList.remove("hidden");
  positionTooltip(el, x, y);
}
function positionTooltip(el, x, y) {
  const pad = 12;
  const r = el.getBoundingClientRect();
  let nx = x + pad, ny = y + pad;
  if (nx + r.width > window.innerWidth - 10) nx = x - r.width - pad;
  if (ny + r.height > window.innerHeight - 10) ny = y - r.height - pad;
  el.style.left = Math.max(4, nx) + "px";
  el.style.top = Math.max(4, ny) + "px";
}
function bindMatchHover(root) {
  let current = null;
  root.addEventListener("mouseover", (e) => {
    const li = e.target.closest(".match-item");
    if (!li || li === current) return;
    current = li;
    try {
      const m = JSON.parse(decodeURIComponent(li.dataset.match));
      renderTooltip(m, e.clientX, e.clientY);
    } catch {}
  });
  root.addEventListener("mousemove", (e) => {
    if (!current) return;
    const el = tooltipEl();
    if (!el.classList.contains("hidden")) positionTooltip(el, e.clientX, e.clientY);
  });
  root.addEventListener("mouseout", (e) => {
    const li = e.target.closest(".match-item");
    if (!li) return;
    if (e.relatedTarget && li.contains(e.relatedTarget)) return;
    current = null;
    hideTooltip();
  });
}

function showDistrictPanel(name) {
  const panel = document.getElementById("district-panel");
  const per = (lastResult && lastResult.per_district) || {};
  const s = per[name];
  if (!s) {
    panel.innerHTML = `<span class="close" id="close-panel">×</span>
      <h2>${esc(name)}</h2>
      <div style="color:var(--text-soft);margin-top:8px">Run a query to see statistics.</div>`;
  } else {
    const pct = (s.fraction * 100).toFixed(1);
    const groups = groupByCategory(s.match_examples || []);
    const groupsHtml = groups.map(g => {
      const shown = g.items.slice(0, 150);
      const more = g.items.length > shown.length
        ? `<li class="hint" style="padding:4px 8px">+${g.items.length - shown.length} more · download CSV for full list</li>` : "";
      const items = shown.map(matchItemHtml).join("");
      return `<div class="match-group">
        <div class="match-group-head">${esc(g.category)}<span class="match-group-count">${g.items.length}</span></div>
        <ul class="match-list">${items}${more}</ul>
      </div>`;
    }).join("");
    const ex = (s.examples || []).map(x => `<li class="plain">${esc(x)}</li>`).join("");
    panel.innerHTML = `
      <span class="close" id="close-panel">×</span>
      <h2>${esc(name)}</h2>
      <div class="headline">
        <span class="headline-frac">${pct}%</span>
        <span class="headline-sub">${s.matches} of ${s.count} locations</span>
      </div>
      ${groupsHtml ? `<h3>Matching names (${s.matches})</h3>${groupsHtml}` : "<h3>No matches in this district</h3>"}
      ${ex ? `<h3>Sample of all names</h3><ul class="match-list">${ex}</ul>` : ""}
    `;
    bindMatchHover(panel);
  }
  panel.classList.remove("hidden");
  document.getElementById("close-panel").addEventListener("click", () => {
    panel.classList.add("hidden");
    selectedDistrict = null;
    applyStyling();
    renderTable();
  });
}

// ---------------- CSV ----------------

function csvEscape(v) {
  if (v == null) return "";
  const s = String(v);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}
function downloadBlob(content, filename) {
  const blob = new Blob(["﻿" + content], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
function downloadSummaryCSV() {
  if (!lastResult) return;
  const rows = [["district", "count", "matches", "fraction"]];
  for (const [name, s] of Object.entries(lastResult.per_district)) {
    rows.push([name, s.count, s.matches, s.fraction.toFixed(6)]);
  }
  downloadBlob(rows.map(r => r.map(csvEscape).join(",")).join("\n"),
               `namescape_${currentCityId}_summary.csv`);
}
function downloadMatchesCSV() {
  if (!lastResult) return;
  const rows = [[
    "district","category","name","address","website","instagram",
    "operator","operator_type","old_name","alt_name","lat","lon",
    "osm_type","osm_id","osm_url","last_edited",
  ]];
  const typeMap = { n:"node", w:"way", r:"relation" };
  for (const [district, s] of Object.entries(lastResult.per_district)) {
    for (const m of (s.match_examples || [])) {
      rows.push([
        district, m.c || "", m.n, m.a || "",
        m.w || "", m.ig || "", m.on || "", m.ot || "",
        m.old || "", m.alt || "", m.lat, m.lon,
        typeMap[m.otype] || "", m.oid || "",
        (m.otype && m.oid) ? `https://www.openstreetmap.org/${typeMap[m.otype]}/${m.oid}` : "",
        m.ts || "",
      ]);
    }
  }
  downloadBlob(rows.map(r => r.map(csvEscape).join(",")).join("\n"),
               `namescape_${currentCityId}_matches.csv`);
}

// ---------------- loading overlay ----------------

let _overlay = null;
function showLoadingOverlay(text) {
  if (!_overlay) {
    _overlay = document.createElement("div");
    _overlay.className = "loading-overlay";
    document.getElementById("main").appendChild(_overlay);
  }
  _overlay.textContent = text || "Loading…";
  _overlay.style.display = "flex";
}
function hideLoadingOverlay() { if (_overlay) _overlay.style.display = "none"; }

// ---------------- boot ----------------

document.addEventListener("DOMContentLoaded", init);
