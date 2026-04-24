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
let currentMetric = "fraction";   // "fraction" or "count"
let minMatches = 0;                // grey out districts with fewer matches than this
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
  document.querySelectorAll('input[name="metric"]').forEach(el => {
    el.addEventListener("change", () => {
      currentMetric = el.value;
      applyStyling(); renderLegend(); renderTable();
    });
  });
  document.getElementById("min-matches").addEventListener("input", e => {
    const v = parseInt(e.target.value, 10);
    minMatches = Number.isFinite(v) && v >= 0 ? v : 0;
    applyStyling(); renderLegend(); renderTable();
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

  showLoadingOverlay(`Loading ${meta.label}…`);
  try {
    currentCityBundle = await fetchCityBundle(cityId);
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
  refreshTagCounts();
  setStatus(`Ready. ${currentCityBundle.elements.length.toLocaleString()} locations in ${currentCityBundle.districts.features.length} ${currentCityBundle.meta.subdivision}s.`);
}

async function fetchCityBundle(cityId) {
  // Prefer the gzipped bundle. Modern browsers decompress via DecompressionStream;
  // if the response headers say Content-Encoding: gzip the browser already
  // decompressed transparently and we can call .json() directly.
  const gzPath = `data/${cityId}.json.gz`;
  const r = await fetch(gzPath);
  if (r.ok) {
    if ((r.headers.get("content-encoding") || "").includes("gzip")) {
      return await r.json();
    }
    if (typeof DecompressionStream !== "undefined") {
      const ds = new DecompressionStream("gzip");
      const stream = r.body.pipeThrough(ds);
      const text = await new Response(stream).text();
      return JSON.parse(text);
    }
    throw new Error("Browser lacks DecompressionStream — please use a modern browser");
  }
  // Legacy fallback: uncompressed bundle.
  const fallback = await fetch(`data/${cityId}.json`);
  if (!fallback.ok) throw new Error(`bundle not found (HTTP ${r.status} for .gz, ${fallback.status} for .json)`);
  return await fallback.json();
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
    catDiv.dataset.catId = cat.id;
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
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.className = "tag-cb";
      cb.dataset.tag = JSON.stringify(tag);
      cb.dataset.specId = specId(tag);
      row.appendChild(cb);
      const sp = document.createElement("span"); sp.className = "tag-label"; sp.textContent = tag.label; row.appendChild(sp);
      const tagN = document.createElement("span"); tagN.className = "tag-count"; row.appendChild(tagN);
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

function refreshTagCounts() {
  if (!currentCityBundle) return;
  const counts = new Map();
  for (const el of currentCityBundle.elements) {
    counts.set(el.s, (counts.get(el.s) || 0) + 1);
  }
  // Per-tag counts (next to the tag label).
  document.querySelectorAll("#tag-tree .tag-row").forEach(row => {
    const cb = row.querySelector(".tag-cb");
    const span = row.querySelector(".tag-count");
    if (!cb || !span) return;
    const n = counts.get(cb.dataset.specId) || 0;
    span.textContent = n ? ` (${n.toLocaleString()})` : " (0)";
    span.classList.toggle("zero", n === 0);
  });
  // Per-category total (sum of its tags' counts) goes in the header counter
  // alongside the existing "checked/total" indicator.
  document.querySelectorAll("#tag-tree .category").forEach(catDiv => {
    const tagBoxes = catDiv.querySelectorAll(".tag-cb");
    let total = 0;
    tagBoxes.forEach(cb => { total += counts.get(cb.dataset.specId) || 0; });
    const headerCount = catDiv.querySelector(".cat-count");
    if (headerCount && !headerCount.classList.contains("active")) {
      headerCount.textContent = `${tagBoxes.length} · ${total.toLocaleString()}`;
    }
    catDiv.dataset.totalCount = total;
  });
}

function toggleAll(on) {
  // Skip the "Uncategorized" super-category — its records are intentionally
  // off by default and should stay opt-in.
  document.querySelectorAll("#tag-tree .category").forEach(catDiv => {
    if (catDiv.dataset.catId === "external_other") {
      // Always reset its in-tree state on a global toggle so the user
      // doesn't end up in a half-checked state by surprise.
      catDiv.querySelectorAll(".tag-cb").forEach(cb => { cb.checked = false; });
      const headerCb = catDiv.querySelector(".cat-cb");
      if (headerCb) { headerCb.checked = false; headerCb.indeterminate = false; }
      return;
    }
    catDiv.querySelectorAll(".tag-cb").forEach(cb => { cb.checked = on; });
    const headerCb = catDiv.querySelector(".cat-cb");
    if (headerCb) { headerCb.checked = on; headerCb.indeterminate = false; }
    const c = catDiv.querySelector(".cat-count");
    if (c) {
      const list = catDiv.querySelectorAll(".tag-cb");
      c.textContent = on ? `${list.length}/${list.length}` : list.length;
      c.classList.toggle("active", on);
    }
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
function metricValue(s) {
  return currentMetric === "fraction" ? s.fraction : s.matches;
}
function isIncluded(s) {
  return s && s.count > 0 && s.matches >= minMatches;
}
function scaleMax() {
  const fracDefault = currentMetric === "fraction" ? 1.0 : 1;
  if (currentMetric === "fraction" && currentScale === "absolute") return 1.0;
  if (!lastResult) return fracDefault;
  let mx = 0;
  for (const s of Object.values(lastResult.per_district)) {
    if (!isIncluded(s)) continue;
    const v = metricValue(s);
    if (v > mx) mx = v;
  }
  return mx > 0 ? mx : fracDefault;
}
function colorFor(s) {
  const mx = scaleMax();
  return sampleStops(paletteStops(), mx > 0 ? metricValue(s) / mx : 0);
}

// Display fractions in per-mille (‰) — much more readable than % for the
// kind of small ratios we see here (a few matches out of tens of thousands).
function fmtFrac(fraction) { return `${(fraction * 1000).toFixed(2)}‰`; }

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
      const n = el._nn || (el._nn = normalize(el.ns ? `${el.n} | ${el.ns}` : el.n));
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
    return (el) => {
      const hay = el.ns ? `${el.n} | ${el.ns}` : el.n;
      return regs.some(r => r.test(hay));
    };
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
  const overall = s.elements > 0 ? fmtFrac(s.matches / s.elements) : fmtFrac(0);
  document.getElementById("summary-stats").innerHTML = `
    <div class="stat-cell"><span class="stat-value">${s.elements.toLocaleString()}</span><span class="stat-label">locations</span></div>
    <div class="stat-cell"><span class="stat-value">${s.matches.toLocaleString()}</span><span class="stat-label">matches</span></div>
    <div class="stat-cell"><span class="stat-value">${overall}</span><span class="stat-label">overall</span></div>
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
    if (!isIncluded(s)) {
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
        fillColor: colorFor(s),
        fillOpacity: 0.78,
        dashArray: null,
      };
    }
    layer.setStyle(style);
    let tt;
    if (!s || s.count === 0) {
      tt = `<b>${name}</b><br/><span style="opacity:0.7">no locations</span>`;
    } else {
      const base = `<b>${name}</b><br/>${s.matches} / ${s.count} = ${fmtFrac(s.fraction)}`;
      tt = (s.matches < minMatches)
        ? `${base}<br/><span style="opacity:0.7">below threshold (${minMatches})</span>`
        : base;
    }
    layer.unbindTooltip();
    layer.bindTooltip(tt, { sticky: true, className: "district-tooltip" });
  });
}

function renderLegend() {
  const el = document.getElementById("legend");
  const stops = paletteStops();
  const grad = `linear-gradient(to right, ${stops.join(", ")})`;
  const mx = scaleMax();
  const isFrac = currentMetric === "fraction";
  const scaleLabel = (isFrac && currentScale === "auto") || (!isFrac)
    ? " · auto-scaled" : "";
  const title = isFrac ? "Fraction matching" : "Total matches";
  const fmt = (v) => isFrac ? fmtFrac(v) : Math.round(v).toLocaleString();
  const nodataLabel = minMatches > 0
    ? `no locations · or fewer than ${minMatches} matches`
    : "no locations in district";
  el.innerHTML = `
    <div class="legend-title">${title}${scaleLabel}</div>
    <div class="legend-bar" style="background:${grad}"></div>
    <div class="legend-scale">
      <span>${isFrac ? fmtFrac(0) : "0"}</span>
      <span>${fmt(mx * 0.5)}</span>
      <span>${fmt(mx)}</span>
    </div>
    <div class="legend-nodata">
      <div class="legend-nodata-swatch"></div>
      <span>${nodataLabel}</span>
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
    const excluded = !noData && r.matches < minMatches;
    const greyed = noData || excluded;
    const pct = noData ? "—" : fmtFrac(r.fraction);
    const color = greyed ? "#d9dde7" : colorFor(r);
    const bar = greyed ? 0 : Math.max(2, metricValue(r) / Math.max(scaleMax(), 1e-9) * 100);
    const activeCls = selectedDistrict === r.name ? " active" : "";
    const ndCls = greyed ? " nodata" : "";
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
  const t = typeMap[m.otype];
  if (!t) return null;  // 'x' = external (Overture/FSQ): no OSM link
  return `https://www.openstreetmap.org/${t}/${m.oid}`;
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
  // Show alt names inline when present — these are part of the search
  // surface, so the user can see why a record matched even if its
  // primary display name doesn't contain the keyword.
  const altHtml = m.ns
    ? `<span class="match-alt"> · ${esc(m.ns)}</span>`
    : "";
  return `<li class="match-item" data-match="${payload}">${nameHtml}${altHtml}</li>`;
}

let _tooltipEl = null;
let _hoverTimer = null;

function tooltipEl() {
  if (_tooltipEl) return _tooltipEl;
  _tooltipEl = document.createElement("div");
  _tooltipEl.className = "match-tooltip hidden";
  // Keep the tooltip open while the cursor is on it, so users can click links.
  _tooltipEl.addEventListener("mouseenter", cancelHide);
  _tooltipEl.addEventListener("mouseleave", scheduleHide);
  document.body.appendChild(_tooltipEl);
  return _tooltipEl;
}

function cancelHide() {
  if (_hoverTimer) { clearTimeout(_hoverTimer); _hoverTimer = null; }
}
function scheduleHide() {
  cancelHide();
  _hoverTimer = setTimeout(() => {
    if (_tooltipEl) {
      _tooltipEl.classList.add("hidden");
      _tooltipEl.dataset.for = "";
    }
  }, 180);
}

function renderTooltipForItem(m, itemEl) {
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
  positionBesideItem(el, itemEl);
}

function positionBesideItem(el, itemEl) {
  // Place to the right of the item; flip to the left if it would overflow.
  // Render briefly off-screen first to measure.
  el.style.left = "-9999px";
  el.style.top = "-9999px";
  const itemRect = itemEl.getBoundingClientRect();
  const ttRect = el.getBoundingClientRect();
  const pad = 8;
  let nx = itemRect.right + pad;
  let ny = itemRect.top;
  if (nx + ttRect.width > window.innerWidth - 10) {
    nx = itemRect.left - ttRect.width - pad;
  }
  if (ny + ttRect.height > window.innerHeight - 10) {
    ny = Math.max(10, window.innerHeight - ttRect.height - 10);
  }
  el.style.left = Math.max(4, nx) + "px";
  el.style.top = Math.max(4, ny) + "px";
}

function bindMatchHover(root) {
  root.addEventListener("mouseover", (e) => {
    const li = e.target.closest(".match-item");
    if (!li) return;
    const el = tooltipEl();
    cancelHide();
    // Avoid flicker: if we're already showing this item's tooltip, do nothing.
    if (el.dataset.for === li.dataset.match) return;
    try {
      const m = JSON.parse(decodeURIComponent(li.dataset.match));
      renderTooltipForItem(m, li);
      el.dataset.for = li.dataset.match;
    } catch {}
  });
  root.addEventListener("mouseout", (e) => {
    const li = e.target.closest(".match-item");
    if (!li) return;
    // If cursor is moving into the tooltip, don't hide.
    if (e.relatedTarget && _tooltipEl && _tooltipEl.contains(e.relatedTarget)) return;
    scheduleHide();
  });
  root.addEventListener("click", (e) => {
    // Don't hijack clicks on the embedded OSM link.
    if (e.target.closest("a")) return;
    const li = e.target.closest(".match-item");
    if (!li) return;
    try {
      const m = JSON.parse(decodeURIComponent(li.dataset.match));
      showMatchOnMap(m);
    } catch {}
  });
}

let _matchMarker = null;

function showMatchOnMap(m) {
  if (!map || typeof m.lat !== "number" || typeof m.lon !== "number") return;
  if (_matchMarker) { map.removeLayer(_matchMarker); _matchMarker = null; }
  _matchMarker = L.circleMarker([m.lat, m.lon], {
    radius: 7,
    color: "#1a1e2c",
    weight: 2,
    fillColor: "#ffd24a",
    fillOpacity: 0.95,
    pane: "markerPane",
  }).addTo(map);
  const altLine = m.ns ? `<div class="popup-alt">${esc(m.ns)}</div>` : "";
  _matchMarker.bindPopup(
    `<div class="popup-name">${esc(m.n)}</div>` +
    `<div class="popup-cat">${esc(m.c || "")}</div>` +
    altLine,
    { closeButton: true, autoClose: false }
  ).openPopup();
  // Pan (don't zoom) so the user keeps their context.
  map.panTo([m.lat, m.lon], { animate: true });
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
    const pct = fmtFrac(s.fraction);
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
        <span class="headline-frac">${pct}</span>
        <span class="headline-sub">${s.matches} of ${s.count} locations</span>
      </div>
      ${groupsHtml ? `<h3>Matching names (${s.matches})</h3>${groupsHtml}` : "<h3>No matches in this district</h3>"}
      ${ex ? `<h3>Sample of all names</h3><ul class="match-list">${ex}</ul>` : ""}
    `;
    bindMatchHover(panel);
  }
  panel.classList.remove("hidden");
}

// Panel close + per-match clicks are delegated on the panel itself so we
// don't depend on re-binding after each innerHTML replace.
(function bindPanelDelegation() {
  const panel = document.getElementById("district-panel");
  if (!panel || panel.dataset.boundClose) return;
  panel.dataset.boundClose = "1";
  panel.addEventListener("click", (e) => {
    if (e.target.closest("#close-panel")) {
      panel.classList.add("hidden");
      selectedDistrict = null;
      if (_matchMarker) { map.removeLayer(_matchMarker); _matchMarker = null; }
      applyStyling();
      renderTable();
    }
  });
})();

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
    "district","category","name","alt_names","source","also_in","address",
    "website","instagram","operator","operator_type","old_name","alt_name",
    "lat","lon","osm_type","osm_id","osm_url","last_edited",
  ]];
  const typeMap = { n:"node", w:"way", r:"relation" };
  const sourceFromSpec = (s) => {
    if (!s) return "osm";
    if (s === "external=overture") return "overture";
    if (s === "external=fsq") return "fsq";
    return "osm";
  };
  for (const [district, s] of Object.entries(lastResult.per_district)) {
    for (const m of (s.match_examples || [])) {
      const osmT = typeMap[m.otype] || "";
      const osmU = osmT && m.oid ? `https://www.openstreetmap.org/${osmT}/${m.oid}` : "";
      rows.push([
        district, m.c || "", m.n, m.ns || "",
        sourceFromSpec(m.s), m.src2 || "",
        m.a || "", m.w || "", m.ig || "", m.on || "", m.ot || "",
        m.old || "", m.alt || "", m.lat, m.lon,
        osmT, m.oid || "", osmU, m.ts || "",
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
