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
from typing import Dict, List, Optional

import httpx
import osm2geojson
import snowballstemmer
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from backend.overpass import (
    CATEGORY_KEYS,
    KEEP_TAGS,
    OVERPASS_URL,
    USER_AGENT,
    is_open,
)
from backend.palettes import PALETTES
from backend.tags_config import TAG_CATEGORIES

from .cities import CITIES, CITY_BY_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build")

SITE_DIR = Path(__file__).parent / "site"
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
# Stemming / normalization (mirror of backend/matching.py, kept in-module
# so tokens match whatever JS runtime does)
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
    """Return (category_label, spec_id) if tags match any spec."""
    for spec in ordered_specs:
        if _spec_matches(spec, tags):
            sid = _spec_id(spec)
            return spec.get("label") or sid, sid
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


def process_city(city: dict, force: bool) -> Optional[dict]:
    out_path = DATA_DIR / f"{city['id']}.json"
    if out_path.exists() and not force:
        log.info("[%s] already built: %s — skipping (use --force to rebuild)",
                 city["id"], out_path.relative_to(SITE_DIR.parent.parent))
        # still return metadata for the config index
        with open(out_path, "r", encoding="utf-8") as f:
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
    districts_gj = load_districts(city)
    n_districts = len(districts_gj["features"])
    log.info("[%s] %d district polygons", city["id"], n_districts)
    if n_districts < 3:
        log.error("[%s] only %d districts — check admin_level / polygon_source", city["id"], n_districts)
        return None

    raw_elements = fetch_pois(city, districts_gj)

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
    elements = []
    skipped_closed = 0
    skipped_nodistrict = 0
    language = city["stemmer_language"]

    for el in raw_elements:
        tagdict = el.get("tags") or {}
        name = tagdict.get("name") or tagdict.get("name:tr") or tagdict.get("name:en")
        if not name:
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
        d = assign_district(lat, lon)
        if d is None:
            skipped_nodistrict += 1
            continue

        cat = category_label_and_spec(tagdict, specs)
        if cat is None:
            continue
        cat_label, spec_id = cat

        rec = {
            "n": name,
            "d": d,
            "c": cat_label,
            "s": spec_id,  # spec id so the frontend can filter by selected tag spec
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "stems": stems_for(name, language),
            "oid": el["id"],
            "otype": el["type"][0],  # 'n'/'w'/'r'
        }
        addr = _format_address(tagdict)
        if addr: rec["a"] = addr
        site = _first(tagdict, ["website", "contact:website"])
        if site: rec["w"] = site
        ig = _first(tagdict, ["instagram", "contact:instagram"])
        if ig: rec["ig"] = ig
        if tagdict.get("operator:type"): rec["ot"] = tagdict["operator:type"]
        if tagdict.get("operator"): rec["on"] = tagdict["operator"]
        if tagdict.get("old_name"): rec["old"] = tagdict["old_name"]
        if tagdict.get("alt_name"): rec["alt"] = tagdict["alt_name"]
        if el.get("timestamp"): rec["ts"] = el["timestamp"][:10]
        elements.append(rec)

    log.info("[%s] %d open POIs in districts (skipped %d closed, %d outside any district)",
             city["id"], len(elements), skipped_closed, skipped_nodistrict)

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
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, separators=(",", ":"))
    size_mb = out_path.stat().st_size / (1024 * 1024)
    log.info("[%s] wrote %s (%.1f MB)", city["id"], out_path.name, size_mb)

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
    args = ap.parse_args()

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
            summary = process_city(city, args.force)
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
        out_path = DATA_DIR / f"{c['id']}.json"
        if out_path.exists():
            with open(out_path, "r", encoding="utf-8") as f:
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
