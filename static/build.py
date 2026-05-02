"""Build per-city data files for the static Namescape site.

For each city in static/cities.py:
  1. Fetch district polygons (OSM relation query OR direct GeoJSON URL).
  2. Fetch all named, currently-open POIs across the category keys we care
     about (shop, amenity, tourism, office, leisure, railway, aeroway,
     historic, craft) in one Overpass query.
  3. Assign each POI to a district via point-in-polygon.
  4. Pre-stem name tokens using Snowball for that city's language.
  5. Write static/site/data/<city>.json  (one bundle per city).

Also writes static/site/data/config.json with tag categories, palettes,
and the city metadata index used by the frontend on startup.

Run:
    python -m static.build
    python -m static.build --only istanbul,paris
    python -m static.build --force           # re-fetch cached cities
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
import osm2geojson
import snowballstemmer
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from .overpass import (
    CATEGORY_KEYS,
    KEEP_TAGS,
    NAME_KEYS,
    OVERPASS_URL,
    USER_AGENT,
    is_open,
)
from .palettes import PALETTES
from .tags_config import TAG_CATEGORIES
from .cities import CITIES, CITY_BY_ID
from . import merge as merge_mod
from . import overture, foursquare
from .category_map import map_external as _map_external_category

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build")

SITE_DIR = Path(__file__).parent / "site"
# Default data dir; main() may rebind this to data/<variant>/ when --variant is set.
DATA_DIR = SITE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# Overpass mirrors, tried in order on failure.
OVERPASS_MIRRORS = [
    OVERPASS_URL,                                          # main
    "https://overpass.kumi.systems/api/interpreter",       # kumi
    "https://overpass.private.coffee/api/interpreter",     # private.coffee
]


def overpass_post(query: str, long_timeout: bool = False) -> dict:
    """POST an Overpass query with retries, exponential backoff, and
    automatic mirror fallback on persistent 429/504."""
    timeout = httpx.Timeout(900.0 if long_timeout else 300.0, connect=30.0)
    last_err: Optional[Exception] = None
    for mirror in OVERPASS_MIRRORS:
        backoff = 30
        for attempt in range(4):
            try:
                with httpx.Client(timeout=timeout, headers=HEADERS) as c:
                    r = c.post(mirror, data={"data": query})
                if r.status_code in (429, 502, 503, 504):
                    log.warning("  %s -> HTTP %d; sleeping %ds (try %d)",
                                mirror, r.status_code, backoff, attempt + 1)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 300)
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.HTTPError as e:
                last_err = e
                log.warning("  %s -> %s; sleeping %ds (try %d)",
                            mirror, e.__class__.__name__, backoff, attempt + 1)
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)
        log.warning("  giving up on mirror %s; trying next", mirror)
    raise RuntimeError(f"All Overpass mirrors failed (last: {last_err})")

# ----------------------------------------------------------------------
# Stemming / normalization (kept in-module so tokens match the JS runtime)
# ----------------------------------------------------------------------

_TR_MAP = str.maketrans({
    "ş": "s", "Ş": "s",
    "ı": "i", "İ": "i",
    "ğ": "g", "Ğ": "g",
    "ç": "c", "Ç": "c",
    "ö": "o", "Ö": "o",
    "ü": "u", "Ü": "u",
})

_TOKEN_RE = re.compile(r"[A-Za-z0-9À-ɏͰ-ϿЀ-ӿ]+")


def normalize(text: str) -> str:
    if not text:
        return ""
    t = text.translate(_TR_MAP).lower()
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


def tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(normalize(text))


_stemmers: Dict[str, object] = {}


def stems_for(text: str, language: str) -> List[str]:
    stemmer = _stemmers.get(language)
    if stemmer is None:
        stemmer = snowballstemmer.stemmer(language)
        _stemmers[language] = stemmer
    toks = tokens(text)
    out = []
    seen = set()
    for t in toks:
        s = stemmer.stemWord(t)
        if not s:
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ----------------------------------------------------------------------
# Polygon fetching
# ----------------------------------------------------------------------

def fetch_osm_districts(relation_id: int, admin_level: int, name_field: str) -> dict:
    query = f"""
[out:json][timeout:300];
rel({relation_id})->.city;
.city map_to_area->.city_area;
relation["admin_level"="{admin_level}"]["boundary"="administrative"](area.city_area);
out body;
>;
out skel qt;
""".strip()
    log.info("  Overpass: districts relation=%d admin_level=%d", relation_id, admin_level)
    raw = overpass_post(query, long_timeout=True)
    gj = osm2geojson.json2geojson(raw)
    features = []
    seen = set()
    for feat in gj.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        props = feat.get("properties", {}) or {}
        tags = props.get("tags", props) or {}
        name = tags.get(name_field) or tags.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        features.append({
            "type": "Feature",
            "properties": {"name": name, "name_en": tags.get("name:en")},
            "geometry": geom,
        })
    return {"type": "FeatureCollection", "features": features}


def fetch_url_districts(url: str, name_field: str) -> dict:
    log.info("  HTTP: districts from %s", url)
    with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0), headers=HEADERS, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        gj = r.json()
    features = []
    seen = set()
    for feat in gj.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        props = feat.get("properties", {}) or {}
        raw = props.get(name_field) or props.get("name") or props.get("NAME")
        if not raw:
            continue
        name = str(raw).strip().title() if raw.isupper() else str(raw).strip()
        if name in seen:
            continue
        seen.add(name)
        features.append({
            "type": "Feature",
            "properties": {"name": name},
            "geometry": geom,
        })
    return {"type": "FeatureCollection", "features": features}


def load_districts(city: dict) -> dict:
    src = city["polygon_source"]
    if src["type"] == "osm":
        return fetch_osm_districts(src["relation_id"], src["admin_level"], src.get("name_field", "name"))
    if src["type"] == "url":
        return fetch_url_districts(src["url"], src["name_field"])
    raise ValueError(f"Unknown polygon_source type: {src['type']}")


# ----------------------------------------------------------------------
# POI fetching
# ----------------------------------------------------------------------

def bbox_from_geojson(gj: dict) -> tuple:
    lats, lons = [], []
    for feat in gj["features"]:
        for coord_ring in _flatten_coords(feat["geometry"]):
            for lon, lat in coord_ring:
                lats.append(lat)
                lons.append(lon)
    return (min(lats), min(lons), max(lats), max(lons))


def _flatten_coords(geom: dict):
    """Yield coord rings from a (Multi)Polygon."""
    t = geom["type"]
    c = geom["coordinates"]
    if t == "Polygon":
        for ring in c:
            yield ring
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                yield ring


def build_poi_query(city: dict, districts_gj: dict) -> str:
    parts = []
    area = city["poi_area"]
    if area["type"] == "osm_relation":
        area_id = 3600000000 + int(area["relation_id"])
        area_decl = f"area({area_id})->.il;\n"
        selector_filter = "(area.il)"
    elif area["type"] == "derive_from_polygons":
        s, w, n, e = bbox_from_geojson(districts_gj)
        area_decl = ""
        selector_filter = f"({s:.5f},{w:.5f},{n:.5f},{e:.5f})"
    else:
        raise ValueError(f"Unknown poi_area: {area}")
    for k in CATEGORY_KEYS:
        for elem in ("node", "way", "relation"):
            parts.append(f'  {elem}["{k}"]{selector_filter};')
    body = "\n".join(parts)
    return (
        f"[out:json][timeout:600];\n"
        f"{area_decl}"
        f"(\n{body}\n);\n"
        f"out center meta;\n"
    )


def fetch_pois(city: dict, districts_gj: dict) -> list:
    query = build_poi_query(city, districts_gj)
    log.info("  Overpass: fetching POIs (this usually takes 30–180s)...")
    t0 = time.time()
    data = overpass_post(query, long_timeout=True)
    elements = data.get("elements", [])
    log.info("  Overpass returned %d raw elements in %.1fs", len(elements), time.time() - t0)
    return elements


# ----------------------------------------------------------------------
# Category attribution (reuses TAG_CATEGORIES for labels)
# ----------------------------------------------------------------------

def _ordered_specs() -> List[dict]:
    specs = []
    for cat in TAG_CATEGORIES:
        for t in cat["tags"]:
            specs.append({**t, "category": cat["label"]})
    # most specific first
    return sorted(
        specs,
        key=lambda s: (
            0 if s.get("filter_key") else 1,
            0 if s["value"] != "*" else 1,
        ),
    )


def _spec_matches(spec: dict, tags: dict) -> bool:
    v = tags.get(spec["key"])
    if not v:
        return False
    if spec["value"] != "*" and v != spec["value"]:
        return False
    fk = spec.get("filter_key")
    if fk and tags.get(fk) != spec.get("filter_value"):
        return False
    return True


def category_label_and_spec(tags: dict, ordered_specs: List[dict]) -> Optional[tuple]:
    """Return (category_label, spec_id) if tags match any spec.

    OSM records whose primary tag doesn't match any spec in tags_config.py
    get bucketed under `external=other` — same as Overture/FSQ records that
    didn't map. Keeps the UI honest: every record either fits a labeled
    checkbox or lives in the single "Uncategorized" group.
    """
    for spec in ordered_specs:
        if _spec_matches(spec, tags):
            sid = _spec_id(spec)
            return spec.get("label") or sid, sid
    for k in CATEGORY_KEYS:
        v = tags.get(k)
        if v:
            return (f"{k}={v}", "external=other")
    return None


def _spec_id(spec: dict) -> str:
    sid = f"{spec['key']}={spec['value']}"
    if spec.get("filter_key"):
        sid += f"|{spec['filter_key']}={spec['filter_value']}"
    return sid


# ----------------------------------------------------------------------
# Per-city processing
# ----------------------------------------------------------------------

def _format_address(tags: dict) -> str:
    if tags.get("addr:full"):
        return tags["addr:full"]
    parts = []
    street = tags.get("addr:street")
    hn = tags.get("addr:housenumber")
    if street and hn:
        parts.append(f"{street} {hn}")
    elif street:
        parts.append(street)
    area = tags.get("addr:suburb") or tags.get("addr:district")
    if area:
        parts.append(area)
    if tags.get("addr:postcode"):
        parts.append(tags["addr:postcode"])
    return ", ".join(parts)


def _first(tags: dict, keys: list) -> str:
    for k in keys:
        v = tags.get(k)
        if v:
            return v
    return ""


# ----------------------------------------------------------------------
# Per-source normalization
# ----------------------------------------------------------------------

def _osm_normalize(raw_elements: list, specs: List[dict]) -> Tuple[List[dict], int]:
    """Convert raw Overpass elements into a list of source-neutral records.

    Returns (records, n_skipped_closed). District assignment happens later
    so external sources go through the same PIP path.
    """
    out = []
    skipped_closed = 0
    for el in raw_elements:
        tagdict = el.get("tags") or {}
        name_variants = []
        seen = set()
        for k in NAME_KEYS:
            v = tagdict.get(k)
            if v and v not in seen:
                name_variants.append(v)
                seen.add(v)
        if not name_variants:
            continue
        if not is_open(tagdict):
            skipped_closed += 1
            continue
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        cat = category_label_and_spec(tagdict, specs)
        if cat is None:
            continue
        cat_label, spec_id = cat
        out.append({
            "source": "osm",
            "external_id": f"{el['type']}/{el['id']}",
            "name": name_variants[0],
            "alt_names": name_variants[1:],
            "lat": float(lat),
            "lon": float(lon),
            "category": cat_label,
            "spec_id": spec_id,
            "_osm_tags": tagdict,
            "_osm_type": el["type"],
            "_osm_id": el["id"],
            "_osm_timestamp": el.get("timestamp"),
        })
    return out, skipped_closed


def _to_bundle_record(rec: dict, district: str, language: str,
                      spec_labels: Optional[dict] = None) -> dict:
    """Build the final per-element bundle record from a normalized record.

    `spec_labels` maps spec_id → human label, used to relabel external
    records that were mapped onto an OSM category so they display
    consistently with OSM records under the same checkbox.
    """
    name_variants = [rec["name"]] + list(rec.get("alt_names") or [])
    spec_id = rec["spec_id"]
    raw_cat = rec.get("category") or ""
    # If the external category mapped onto an OSM spec, use the OSM label;
    # otherwise keep the source's raw category string.
    display_cat = (spec_labels or {}).get(spec_id) or raw_cat
    bundle = {
        "n": rec["name"],
        "d": district,
        "c": display_cat,
        "s": spec_id,
        "lat": round(rec["lat"], 6),
        "lon": round(rec["lon"], 6),
        "stems": stems_for(" ".join(name_variants), language),
        "src": rec["source"][0],  # 'o' / 'f' / 'v' for osm/fsq/overture
    }
    if rec["source"] == "osm":
        bundle["oid"] = rec["_osm_id"]
        bundle["otype"] = rec["_osm_type"][0]  # 'n'/'w'/'r'
        tagdict = rec["_osm_tags"]
        addr = _format_address(tagdict)
        if addr: bundle["a"] = addr
        site = _first(tagdict, ["website", "contact:website"])
        if site: bundle["w"] = site
        ig = _first(tagdict, ["instagram", "contact:instagram"])
        if ig: bundle["ig"] = ig
        if tagdict.get("operator:type"): bundle["ot"] = tagdict["operator:type"]
        if tagdict.get("operator"): bundle["on"] = tagdict["operator"]
        if tagdict.get("old_name"): bundle["old"] = tagdict["old_name"]
        if tagdict.get("alt_name"): bundle["alt"] = tagdict["alt_name"]
        ts = rec.get("_osm_timestamp")
        if ts: bundle["ts"] = ts[:10]
    else:
        # Overture / Foursquare: source-id with a 1-letter type prefix so the
        # frontend's "open on OSM" link logic doesn't trip over them.
        bundle["oid"] = rec["external_id"]
        bundle["otype"] = "x"  # external — frontend will skip the OSM link
        if rec.get("address"): bundle["a"] = rec["address"]
        if rec.get("brand"): bundle["on"] = rec["brand"]
    if len(name_variants) > 1:
        bundle["ns"] = " | ".join(name_variants[1:])
    if rec.get("also_in"):
        bundle["src2"] = "+".join(sorted(set(rec["also_in"])))
    return bundle


import gzip as _gzip


# Source-fetch cache: skips re-querying Overpass / Overture / FSQ across builds.
# Bump the version on a fetcher to invalidate that source.
FETCH_CACHE_DIR = SITE_DIR.parent.parent / ".fetch_cache"
FETCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_VERSIONS = {"osm": 1, "districts": 1, "overture": 1, "fsq": 1}


def _cached_fetch(key: str, source: str, fetch_fn, force: bool = False):
    """Read JSON from .fetch_cache/<key>__v<n>.json.gz or run fetch_fn and store it.

    Each source has a version in `_CACHE_VERSIONS`; bumping the version
    invalidates all cached data for that source.
    """
    version = _CACHE_VERSIONS[source]
    path = FETCH_CACHE_DIR / f"{key}__{source}__v{version}.json.gz"
    if path.exists() and not force:
        log.info("  cache hit: .fetch_cache/%s", path.name)
        with _gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    out = fetch_fn()
    with _gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    log.info("  cached: .fetch_cache/%s (%d items)", path.name, len(out))
    return out


def process_city(city: dict, force: bool,
                 sources: Tuple[str, ...] = ("osm", "overture", "fsq"),
                 refetch: bool = False,
                 dedup: bool = True) -> Optional[dict]:
    out_path = DATA_DIR / f"{city['id']}.json.gz"
    if out_path.exists() and not force:
        log.info("[%s] already built: %s — skipping (use --force to rebuild)",
                 city["id"], out_path.relative_to(SITE_DIR.parent.parent))
        # still return metadata for the config index
        with _gzip.open(out_path, "rt", encoding="utf-8") as f:
            existing = json.load(f)
        return {
            "id": city["id"],
            "label": city["label"],
            "country": city["country"],
            "subdivision": city["subdivision"],
            "stemmer_language": city["stemmer_language"],
            "map_center": city["map_center"],
            "map_zoom": city["map_zoom"],
            "n_districts": len(existing["districts"]["features"]),
            "n_elements": len(existing["elements"]),
        }

    log.info("[%s] START", city["id"])
    districts_gj = _cached_fetch(city["id"], "districts",
                                 lambda: load_districts(city), force=refetch)
    n_districts = len(districts_gj["features"])
    log.info("[%s] %d district polygons", city["id"], n_districts)
    if n_districts < 3:
        log.error("[%s] only %d districts — check admin_level / polygon_source", city["id"], n_districts)
        return None

    # Build spatial index for PIP
    geoms = [shape(f["geometry"]) for f in districts_gj["features"]]
    names = [f["properties"]["name"] for f in districts_gj["features"]]
    tree = STRtree(geoms)

    def assign_district(lat: float, lon: float) -> Optional[str]:
        pt = Point(lon, lat)
        for idx in tree.query(pt):
            if geoms[int(idx)].contains(pt):
                return names[int(idx)]
        return None

    specs = _ordered_specs()
    # spec_ids backed by an actual sidebar checkbox. Anything outside this
    # set is demoted to external=other (Uncategorized) so it stays visible.
    ui_spec_ids = {_spec_id(s) for s in specs}
    language = city["stemmer_language"]
    bbox = bbox_from_geojson(districts_gj)

    # 1) OSM
    osm_recs: List[dict] = []
    if "osm" in sources:
        raw_elements = _cached_fetch(city["id"], "osm",
                                     lambda: fetch_pois(city, districts_gj), force=refetch)
        osm_recs, skipped_closed = _osm_normalize(raw_elements, specs)
        log.info("[%s] OSM: %d normalized (%d closed dropped)",
                 city["id"], len(osm_recs), skipped_closed)
    else:
        log.info("[%s] OSM: skipped (not in --sources)", city["id"])

    # 2) Optionally pull external sources for the same bbox.
    overture_recs: List[dict] = []
    fsq_recs: List[dict] = []
    # Note: spec_id is computed from raw category here (post-cache) so cache
    # entries don't need to be invalidated when the category mapping changes.
    from collections import Counter
    unmapped = Counter()  # raw_cat -> count, scoped per (city, source)
    demoted = Counter()   # spec_id -> count for mappings absent from tags_config.py

    def _assign_spec(r: dict, source: str) -> None:
        raw = (r.get("category") or "").strip()
        sid = _map_external_category(raw)
        if sid != "external=other" and sid not in ui_spec_ids:
            # category_map.tsv produced an OSM spec that has no checkbox in
            # tags_config.py. Demote so the record lives under "Uncategorized"
            # rather than being unfilterable in the UI.
            demoted[(source, sid)] += 1
            sid = "external=other"
        r["spec_id"] = sid
        if sid == "external=other" and raw:
            unmapped[(source, raw)] += 1

    if "overture" in sources:
        try:
            raw_overture = _cached_fetch(city["id"], "overture",
                                         lambda: overture.fetch(bbox), force=refetch)
            for r in raw_overture:
                _assign_spec(r, "overture")
                overture_recs.append(r)
        except Exception as e:
            log.warning("[%s] Overture failed (%s) — continuing without it", city["id"], e)
    if "fsq" in sources:
        try:
            raw_fsq = _cached_fetch(city["id"], "fsq",
                                    lambda: foursquare.fetch(bbox), force=refetch)
            for r in raw_fsq:
                _assign_spec(r, "fsq")
                fsq_recs.append(r)
        except Exception as e:
            log.warning("[%s] Foursquare failed (%s) — continuing without it", city["id"], e)

    # Report spec_ids demoted to external=other because tags_config.py has no
    # checkbox for them. To surface them in the UI instead, add the spec to
    # static/tags_config.py.
    if demoted:
        log.info("[%s] demoted %d records to external=other "
                 "(spec_id has no checkbox in tags_config.py):",
                 city["id"], sum(demoted.values()))
        for (src, sid), n in demoted.most_common(20):
            log.info("    %6d  [%s]  %s", n, src, sid)

    # Dump unmapped categories so the user can extend static/category_map.py.
    if unmapped:
        out_txt = FETCH_CACHE_DIR / f"unmapped_categories__{city['id']}.tsv"
        with open(out_txt, "w", encoding="utf-8") as f:
            f.write("count\tsource\traw_category\n")
            for (src, cat), n in unmapped.most_common():
                f.write(f"{n}\t{src}\t{cat}\n")
        top = unmapped.most_common(60)
        log.info("[%s] %d distinct unmapped external categories (%d records); "
                 "top %d below — see %s for full list",
                 city["id"], len(unmapped), sum(unmapped.values()),
                 len(top), out_txt.name)
        for (src, cat), n in top:
            log.info("    %6d  [%s]  %s", n, src, cat)

    # 3) Spatial + name dedup across sources (OSM wins; FSQ over Overture).
    if dedup:
        merged = merge_mod.merge(
            {"osm": osm_recs, "fsq": fsq_recs, "overture": overture_recs},
            priority=("osm", "fsq", "overture"),
        )
    else:
        merged = list(osm_recs) + list(fsq_recs) + list(overture_recs)
        log.info("[%s] dedup skipped: %d records concatenated "
                 "(osm=%d, fsq=%d, overture=%d)",
                 city["id"], len(merged),
                 len(osm_recs), len(fsq_recs), len(overture_recs))

    # 4) Final pass: PIP to district, build bundle records.
    spec_labels = {s["spec_id"] if "spec_id" in s else _spec_id(s):
                   (s.get("label") or _spec_id(s))
                   for s in specs}
    elements = []
    skipped_nodistrict = 0
    for rec in merged:
        d = assign_district(rec["lat"], rec["lon"])
        if d is None:
            skipped_nodistrict += 1
            continue
        elements.append(_to_bundle_record(rec, d, language, spec_labels))

    log.info("[%s] %d POIs in districts (%d outside any district)",
             city["id"], len(elements), skipped_nodistrict)

    bundle = {
        "meta": {
            "id": city["id"],
            "label": city["label"],
            "country": city["country"],
            "subdivision": city["subdivision"],
            "stemmer_language": language,
            "map_center": city["map_center"],
            "map_zoom": city["map_zoom"],
            "n_elements": len(elements),
            "n_districts": n_districts,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "districts": districts_gj,
        "elements": elements,
    }
    with _gzip.open(out_path, "wt", encoding="utf-8", compresslevel=6) as f:
        json.dump(bundle, f, ensure_ascii=False, separators=(",", ":"))
    size_mb = out_path.stat().st_size / (1024 * 1024)
    log.info("[%s] wrote %s (%.1f MB gzipped)", city["id"], out_path.name, size_mb)

    return {
        "id": city["id"],
        "label": city["label"],
        "country": city["country"],
        "subdivision": city["subdivision"],
        "stemmer_language": language,
        "map_center": city["map_center"],
        "map_zoom": city["map_zoom"],
        "n_districts": n_districts,
        "n_elements": len(elements),
    }


# ----------------------------------------------------------------------
# Config.json (shared data: tags, palettes, city index)
# ----------------------------------------------------------------------

def write_config(city_summaries: List[dict]) -> None:
    cfg = {
        "tag_categories": TAG_CATEGORIES,
        "palettes": PALETTES,
        "cities": city_summaries,
    }
    out = DATA_DIR / "config.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, separators=(",", ":"))
    log.info("wrote %s", out.relative_to(SITE_DIR.parent.parent))


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated city ids")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sleep", type=float, default=20.0,
                    help="seconds to sleep between cities to avoid Overpass rate limits")
    ap.add_argument("--sources", default="osm,overture,fsq",
                    help="comma-separated subset of {osm,overture,fsq} "
                         "(default: osm,overture,fsq). Sources not listed are "
                         "neither fetched nor merged into the bundle.")
    ap.add_argument("--refetch", action="store_true",
                    help="ignore .fetch_cache and re-query Overpass / Overture / FSQ "
                         "from scratch (default: reuse cached responses across runs)")
    ap.add_argument("--reindex", action="store_true",
                    help="just rewrite data/config.json from existing .json.gz bundles "
                         "(no Overpass / Overture / FSQ calls)")
    ap.add_argument("--variant", default=None,
                    help="write outputs to data/<variant>/ instead of data/. "
                         "Use to keep parallel builds (e.g. --variant overture_only) "
                         "without overwriting the default bundles.")
    ap.add_argument("--no-dedup", action="store_true",
                    help="skip merge.py — concatenate sources as-is, keep duplicates "
                         "(both cross-source and within-source).")
    args = ap.parse_args()

    # Rebind module-level DATA_DIR if --variant is set so process_city /
    # write_config / reindex all read+write the variant subdirectory.
    if args.variant:
        global DATA_DIR
        DATA_DIR = SITE_DIR / "data" / args.variant
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log.info("variant=%s -> writing to %s", args.variant,
                 DATA_DIR.relative_to(SITE_DIR.parent.parent))

    if args.reindex:
        summaries: List[dict] = []
        for c in CITIES:
            out_path = DATA_DIR / f"{c['id']}.json.gz"
            if not out_path.exists():
                continue
            with _gzip.open(out_path, "rt", encoding="utf-8") as f:
                b = json.load(f)
            summaries.append({
                "id": c["id"], "label": c["label"], "country": c["country"],
                "subdivision": c["subdivision"],
                "stemmer_language": c["stemmer_language"],
                "map_center": c["map_center"], "map_zoom": c["map_zoom"],
                "n_districts": len(b["districts"]["features"]),
                "n_elements": len(b["elements"]),
            })
        order = {c["id"]: i for i, c in enumerate(CITIES)}
        summaries.sort(key=lambda s: order.get(s["id"], 999))
        write_config(summaries)
        log.info("reindexed: %d cities -> data/config.json", len(summaries))
        return 0

    valid_sources = {"osm", "overture", "fsq"}
    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    bad = [s for s in sources if s not in valid_sources]
    if bad or not sources:
        log.error("invalid --sources=%s (must be subset of %s)",
                  args.sources, ",".join(sorted(valid_sources)))
        return 1

    ids = [c["id"] for c in CITIES]
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        ids = [i for i in ids if i in wanted]
        if not ids:
            log.error("no matching city ids in --only=%s", args.only)
            return 1

    summaries: List[dict] = []
    for i, cid in enumerate(ids):
        city = CITY_BY_ID[cid]
        try:
            summary = process_city(city, args.force,
                                   sources=sources,
                                   refetch=args.refetch,
                                   dedup=not args.no_dedup)
        except Exception as e:
            log.exception("[%s] build failed: %s", cid, e)
            continue
        if summary:
            summaries.append(summary)
        if i < len(ids) - 1:
            time.sleep(args.sleep)

    # Fill in summaries for any cities we skipped to maintain a full index
    existing_ids = {s["id"] for s in summaries}
    for c in CITIES:
        if c["id"] in existing_ids:
            continue
        out_path = DATA_DIR / f"{c['id']}.json.gz"
        if out_path.exists():
            with _gzip.open(out_path, "rt", encoding="utf-8") as f:
                b = json.load(f)
            summaries.append({
                "id": c["id"], "label": c["label"], "country": c["country"],
                "subdivision": c["subdivision"],
                "stemmer_language": c["stemmer_language"],
                "map_center": c["map_center"], "map_zoom": c["map_zoom"],
                "n_districts": len(b["districts"]["features"]),
                "n_elements": len(b["elements"]),
            })

    # Stable order per CITIES
    order = {c["id"]: i for i, c in enumerate(CITIES)}
    summaries.sort(key=lambda s: order.get(s["id"], 999))
    write_config(summaries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
