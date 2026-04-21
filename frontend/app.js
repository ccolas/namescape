// Istanbul Voices — frontend

let map;
let districtsLayer;
let paletteMap = {};
let currentPalette = "Viridis";
let currentScale = "auto";           // "auto" or "absolute"
let lastResult = null;                // { per_district, total_elements, unassigned, keywords, tags, mode }
let sortKey = "fraction";
let sortDir = "desc";                 // "desc" or "asc"
let selectedDistrict = null;
let layerByName = {};                 // district name -> leaflet layer

const MODE_HINTS = {
  substring: "Diacritic-insensitive substring. Fastest, loosest. Misses consonant mutations (kitap → kitabı).",
  stemmed:   "Turkish stemmer + diacritic-insensitive. Matches root words across suffix variants.",
  regex:     "Python regex, case-insensitive. Applied to the original name (with diacritics).",
};

// ---------- color interpolation ----------

function hexToRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function rgbToHex([r, g, b]) {
  const h = (r << 16) | (g << 8) | b;
  return "#" + h.toString(16).padStart(6, "0");
}
function lerpHex(a, b, t) {
  const ra = hexToRgb(a), rb = hexToRgb(b);
  return rgbToHex(ra.map((v, i) => Math.round(v + (rb[i] - v) * t)));
}
function sampleStops(stops, t) {
  const f = Math.max(0, Math.min(1, t));
  const x = f * (stops.length - 1);
  const i = Math.floor(x), frac = x - i;
  if (i >= stops.length - 1) return stops[stops.length - 1];
  return lerpHex(stops[i], stops[i + 1], frac);
}
function paletteStops() { return paletteMap[currentPalette] || paletteMap["Viridis"]; }

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
  const t = mx > 0 ? fraction / mx : 0;
  return sampleStops(paletteStops(), t);
}

// ---------- init ----------

async function init() {
  map = L.map("map", { preferCanvas: true, zoomControl: true }).setView([41.05, 29.0], 10);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: 19,
    subdomains: "abcd",
  }).addTo(map);

  try {
    const [tags, pals, geo] = await Promise.all([
      fetch("/api/tags").then(r => r.json()),
      fetch("/api/palettes").then(r => r.json()),
      fetch("/api/districts").then(r => {
        if (!r.ok) throw new Error(`districts unavailable (HTTP ${r.status})`);
        return r.json();
      }),
    ]);
    paletteMap = pals;
    renderPalettes(Object.keys(pals));
    renderTags(tags);
    renderDistrictLayer(geo);
    renderLegend();
    setModeHint();
  } catch (e) {
    setStatus(`Failed to load: ${e.message}`, "error");
  }

  // palette / scale change → instant restyle, no query
  document.getElementById("palette").addEventListener("change", e => {
    currentPalette = e.target.value;
    applyStyling();
    renderLegend();
    renderTable();
  });
  document.querySelectorAll('input[name="scale"]').forEach(el => {
    el.addEventListener("change", () => {
      currentScale = el.value;
      applyStyling();
      renderLegend();
    });
  });
  document.querySelectorAll('input[name="mode"]').forEach(el => {
    el.addEventListener("change", setModeHint);
  });

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
}

function setModeHint() {
  const mode = document.querySelector('input[name="mode"]:checked').value;
  document.getElementById("mode-hint").textContent = MODE_HINTS[mode] || "";
}

// ---------- sidebar: palettes + tags ----------

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
    chevron.innerHTML = '<path fill="currentColor" d="M2 3.5l3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>';
    header.appendChild(chevron);

    const catCb = document.createElement("input");
    catCb.type = "checkbox";
    catCb.className = "cat-cb";
    catCb.addEventListener("click", e => e.stopPropagation());
    header.appendChild(catCb);

    const lbl = document.createElement("span");
    lbl.className = "cat-label";
    lbl.textContent = cat.label;
    header.appendChild(lbl);

    const count = document.createElement("span");
    count.className = "cat-count";
    count.textContent = cat.tags.length;
    header.appendChild(count);

    catDiv.appendChild(header);

    const list = document.createElement("div");
    list.className = "tag-list";

    const tagBoxes = [];
    let masterBox = null;

    for (const tag of cat.tags) {
      const row = document.createElement("label");
      row.className = "tag-row";
      if (tag.value === "*") row.classList.add("master");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "tag-cb";
      cb.dataset.tag = JSON.stringify(tag);
      row.appendChild(cb);
      const sp = document.createElement("span");
      sp.textContent = tag.label;
      row.appendChild(sp);
      list.appendChild(row);
      tagBoxes.push(cb);
      if (tag.value === "*") masterBox = cb;
    }
    catDiv.appendChild(list);
    container.appendChild(catDiv);

    const updateHeader = () => {
      const checkedCount = tagBoxes.filter(b => b.checked).length;
      catCb.checked = checkedCount === tagBoxes.length;
      catCb.indeterminate = checkedCount > 0 && checkedCount < tagBoxes.length;
      count.textContent = checkedCount > 0 ? `${checkedCount}/${tagBoxes.length}` : tagBoxes.length;
      count.classList.toggle("active", checkedCount > 0);
    };

    const setAll = (on) => {
      for (const cb of tagBoxes) cb.checked = on;
      updateHeader();
    };

    // Category-header checkbox: toggle all
    catCb.addEventListener("change", () => setAll(catCb.checked));

    // Header row (not on checkbox): collapse/expand
    header.addEventListener("click", (e) => {
      if (e.target === catCb) return;
      catDiv.classList.toggle("collapsed");
    });

    // Master tag ("All X" / value=*): toggle all siblings (including itself)
    if (masterBox) {
      masterBox.addEventListener("change", () => {
        setAll(masterBox.checked);
      });
    }

    // Any other tag: update header state (and master, if present)
    for (const cb of tagBoxes) {
      if (cb === masterBox) continue;
      cb.addEventListener("change", () => {
        // If user unchecks a sibling while master is on, turn master off
        // (but keep siblings as they are)
        if (masterBox && masterBox.checked && !cb.checked) {
          masterBox.checked = false;
        }
        // If user checks a sibling and all siblings + master are now checked, master stays checked
        // Auto-check master only when every box (incl master) is on
        if (masterBox && !masterBox.checked && tagBoxes.every(b => b.checked || b === masterBox)) {
          masterBox.checked = true;
        }
        updateHeader();
      });
    }
  }
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

// ---------- map ----------

function renderDistrictLayer(geo) {
  districtsLayer = L.geoJSON(geo, {
    style: () => ({
      color: "#8a92a5", weight: 1,
      fillColor: "#d9dde7", fillOpacity: 0.5,
      dashArray: "2,3",
    }),
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
        if (!L.Browser.ie && !L.Browser.opera) layer.bringToFront();
      });
      layer.on("mouseout", () => {
        applyStyling();
      });
    },
  }).addTo(map);
  try { map.fitBounds(districtsLayer.getBounds(), { padding: [20, 20] }); } catch {}
}

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

// ---------- legend ----------

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
      <span>no locations found in district</span>
    </div>
  `;
}

// ---------- district panel ----------

const esc = (x) => (x == null ? "" : String(x).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])));

function osmUrl(m) {
  if (!m || !m.type || !m.osm_id) return null;
  return `https://www.openstreetmap.org/${m.type}/${m.osm_id}`;
}

function groupByCategory(matches) {
  const map = new Map();
  for (const m of matches) {
    const cat = m.category || "other";
    if (!map.has(cat)) map.set(cat, []);
    map.get(cat).push(m);
  }
  const groups = [...map.entries()].map(([category, items]) => {
    items.sort((a, b) => a.name.localeCompare(b.name, "tr"));
    return { category, items };
  });
  groups.sort((a, b) => b.items.length - a.items.length || a.category.localeCompare(b.category));
  return groups;
}

function matchItemHtml(m) {
  const url = osmUrl(m);
  const payload = encodeURIComponent(JSON.stringify(m));
  const nameHtml = url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(m.name)}</a>`
    : `<span>${esc(m.name)}</span>`;
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
function hideTooltip() {
  tooltipEl().classList.add("hidden");
}
function renderTooltip(m, x, y) {
  const el = tooltipEl();
  const rows = [];
  rows.push(`<div class="tt-name">${esc(m.name)}</div>`);
  rows.push(`<div class="tt-cat">${esc(m.category || "")}</div>`);
  if (m.address) rows.push(`<div class="tt-row"><span class="tt-key">address</span><span>${esc(m.address)}</span></div>`);
  if (m.website) {
    const href = m.website.match(/^https?:\/\//) ? m.website : "https://" + m.website;
    rows.push(`<div class="tt-row"><span class="tt-key">website</span><a href="${esc(href)}" target="_blank" rel="noopener">${esc(m.website)}</a></div>`);
  }
  if (m.instagram) {
    const handle = m.instagram.replace(/^@/, "");
    const href = handle.match(/^https?:\/\//) ? handle : `https://instagram.com/${handle.replace(/^https?:\/\/(www\.)?instagram\.com\//, "")}`;
    rows.push(`<div class="tt-row"><span class="tt-key">instagram</span><a href="${esc(href)}" target="_blank" rel="noopener">${esc(m.instagram)}</a></div>`);
  }
  if (m.operator_type) rows.push(`<div class="tt-row"><span class="tt-key">operator:type</span><span>${esc(m.operator_type)}</span></div>`);
  if (m.operator && !m.operator_type) rows.push(`<div class="tt-row"><span class="tt-key">operator</span><span>${esc(m.operator)}</span></div>`);
  if (m.old_name) rows.push(`<div class="tt-row"><span class="tt-key">formerly</span><span>${esc(m.old_name)}</span></div>`);
  if (m.timestamp) rows.push(`<div class="tt-meta">last edited ${esc(m.timestamp.slice(0, 10))}</div>`);
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
  let currentItem = null;
  root.addEventListener("mouseover", (e) => {
    const li = e.target.closest(".match-item");
    if (!li || li === currentItem) return;
    currentItem = li;
    try {
      const m = JSON.parse(decodeURIComponent(li.dataset.match));
      renderTooltip(m, e.clientX, e.clientY);
    } catch {}
  });
  root.addEventListener("mousemove", (e) => {
    if (!currentItem) return;
    const el = tooltipEl();
    if (!el.classList.contains("hidden")) positionTooltip(el, e.clientX, e.clientY);
  });
  root.addEventListener("mouseout", (e) => {
    const li = e.target.closest(".match-item");
    if (!li) return;
    if (e.relatedTarget && li.contains(e.relatedTarget)) return;
    currentItem = null;
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
    const allMatches = s.match_examples || [];
    const groups = groupByCategory(allMatches);
    const groupsHtml = groups.map(g => {
      const shown = g.items.slice(0, 150);
      const more = g.items.length > shown.length
        ? `<li class="hint" style="padding:4px 8px">+${g.items.length - shown.length} more · download CSV for full list</li>` : "";
      const items = shown.map(matchItemHtml).join("");
      return `
        <div class="match-group">
          <div class="match-group-head">${esc(g.category)}<span class="match-group-count">${g.items.length}</span></div>
          <ul class="match-list">${items}${more}</ul>
        </div>`;
    }).join("");
    const ex = (s.examples || []).map(x => `<li>${esc(x)}</li>`).join("");
    panel.innerHTML = `
      <span class="close" id="close-panel">×</span>
      <h2>${esc(name)}</h2>
      <div class="headline">
        <span class="headline-frac">${pct}%</span>
        <span class="headline-sub">${s.matches} of ${s.count} locations</span>
      </div>
      ${groupsHtml
        ? `<h3>Matching names (${s.matches})</h3>${groupsHtml}`
        : "<h3>No matches in this district</h3>"}
      ${ex ? `<h3>Sample of all names</h3><ul class="match-list">${ex.replace(/<li>/g,'<li class="plain">')}</ul>` : ""}
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

// ---------- district table (sidebar) ----------

function renderTable() {
  const tbody = document.querySelector("#district-table tbody");
  if (!tbody) return;
  if (!lastResult) { tbody.innerHTML = ""; return; }
  const rows = Object.entries(lastResult.per_district)
    .map(([name, s]) => ({ name, ...s }));
  rows.sort((a, b) => {
    const dir = sortDir === "asc" ? 1 : -1;
    if (sortKey === "name") return a.name.localeCompare(b.name, "tr") * dir;
    // numeric: put no-data rows last regardless of direction
    if (a.count === 0 && b.count > 0) return 1;
    if (b.count === 0 && a.count > 0) return -1;
    const av = a[sortKey] ?? 0, bv = b[sortKey] ?? 0;
    return (av - bv) * dir;
  });

  document.querySelectorAll("#district-table th").forEach(th => {
    th.classList.toggle("sort-active", th.dataset.sort === sortKey);
    th.classList.toggle("asc", th.dataset.sort === sortKey && sortDir === "asc");
  });

  tbody.innerHTML = rows.map(r => {
    const noData = r.count === 0;
    const pct = noData ? "—" : `${(r.fraction * 100).toFixed(1)}%`;
    const color = noData ? "#d9dde7" : colorFor(r.fraction);
    const barWidth = noData ? 0 : Math.max(2, r.fraction / Math.max(scaleMax(), 1e-9) * 100);
    const activeCls = selectedDistrict === r.name ? " active" : "";
    const nodataCls = noData ? " nodata" : "";
    return `
      <tr class="row${activeCls}${nodataCls}" data-name="${esc(r.name)}">
        <td>${esc(r.name)}</td>
        <td class="num">${r.matches}</td>
        <td class="num">${r.count}</td>
        <td class="num">
          <span class="frac-bar"><span style="width:${barWidth}%;background:${color}"></span></span>
          ${pct}
        </td>
      </tr>
    `;
  }).join("");

  tbody.querySelectorAll("tr").forEach(tr => {
    tr.addEventListener("click", () => {
      const name = tr.dataset.name;
      selectedDistrict = name;
      showDistrictPanel(name);
      const layer = layerByName[name];
      if (layer) {
        try { map.fitBounds(layer.getBounds(), { padding: [40, 40], maxZoom: 13 }); } catch {}
      }
      applyStyling();
      renderTable();
    });
  });
}

// ---------- CSV downloads ----------

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
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function downloadSummaryCSV() {
  if (!lastResult) return;
  const rows = [["district", "count", "matches", "fraction"]];
  for (const [name, s] of Object.entries(lastResult.per_district)) {
    rows.push([name, s.count, s.matches, s.fraction.toFixed(6)]);
  }
  const csv = rows.map(r => r.map(csvEscape).join(",")).join("\n");
  downloadBlob(csv, "istanbul_voices_summary.csv");
}

function downloadMatchesCSV() {
  if (!lastResult) return;
  const rows = [[
    "district", "category", "name", "address",
    "website", "instagram", "operator", "operator_type",
    "old_name", "alt_name", "lat", "lon",
    "osm_type", "osm_id", "osm_url", "last_edited",
  ]];
  for (const [district, s] of Object.entries(lastResult.per_district)) {
    for (const m of (s.match_examples || [])) {
      rows.push([
        district, m.category || "", m.name, m.address || "",
        m.website || "", m.instagram || "", m.operator || "", m.operator_type || "",
        m.old_name || "", m.alt_name || "", m.lat, m.lon,
        m.type || "", m.osm_id || "",
        (m.type && m.osm_id) ? `https://www.openstreetmap.org/${m.type}/${m.osm_id}` : "",
        m.timestamp || "",
      ]);
    }
  }
  const csv = rows.map(r => r.map(csvEscape).join(",")).join("\n");
  downloadBlob(csv, "istanbul_voices_matches.csv");
}

// ---------- query ----------

function selectedTags() {
  return [...document.querySelectorAll("#tag-tree .tag-cb:checked")]
    .map(cb => JSON.parse(cb.dataset.tag));
}
function parseKeywords() {
  return document.getElementById("keywords").value
    .split(/[\n,]/)
    .map(s => s.trim())
    .filter(Boolean);
}
function setStatus(text, cls = "") {
  const el = document.getElementById("status");
  el.textContent = text;
  el.className = "status " + cls;
}

async function runQuery() {
  const tags = selectedTags();
  if (!tags.length) {
    setStatus("Pick at least one location type.", "error");
    return;
  }
  const keywords = parseKeywords();
  const mode = document.querySelector('input[name="mode"]:checked').value;

  const btn = document.getElementById("run");
  btn.disabled = true;
  setStatus(`Querying… first query per tag-set: 30–120s. Cached tag-sets are instant.`, "working");

  const t0 = performance.now();
  let r;
  try {
    r = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags, keywords, mode }),
    });
  } catch (e) {
    setStatus(`Network error: ${e.message}`, "error");
    btn.disabled = false;
    return;
  }
  if (!r.ok) {
    let msg = `Error ${r.status}`;
    try { const j = await r.json(); if (j.detail) msg += `: ${j.detail}`; } catch {}
    setStatus(msg, "error");
    btn.disabled = false;
    return;
  }
  const data = await r.json();
  lastResult = data;
  const dt = ((performance.now() - t0) / 1000).toFixed(1);
  const nonEmpty = Object.values(data.per_district).filter(s => s.count > 0).length;
  const totalMatches = Object.values(data.per_district).reduce((a, s) => a + s.matches, 0);
  setStatus(`Done in ${dt}s.`);

  renderSummaryStats({
    elements: data.total_elements,
    matches: totalMatches,
    nonEmpty, total: Object.keys(data.per_district).length,
    unassigned: data.unassigned,
    seconds: dt,
  });
  document.getElementById("results-card").classList.remove("hidden");
  applyStyling();
  renderLegend();
  renderTable();
  btn.disabled = false;
}

function renderSummaryStats(s) {
  const overall = s.elements > 0 ? ((s.matches / s.elements) * 100).toFixed(1) : "0.0";
  document.getElementById("summary-stats").innerHTML = `
    <div class="stat-cell">
      <span class="stat-value">${s.elements.toLocaleString()}</span>
      <span class="stat-label">locations</span>
    </div>
    <div class="stat-cell">
      <span class="stat-value">${s.matches.toLocaleString()}</span>
      <span class="stat-label">matches</span>
    </div>
    <div class="stat-cell">
      <span class="stat-value">${overall}%</span>
      <span class="stat-label">overall</span>
    </div>
  `;
}

// ---------- boot ----------

document.addEventListener("DOMContentLoaded", init);
