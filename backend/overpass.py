"""Overpass API client with per-tagset caching.

Istanbul area is looked up by its OSM relation id (223474).
In Overpass, areas derived from relations use id = 3600000000 + relation_id.
"""
import asyncio
import gzip
import hashlib
import json
import logging
from pathlib import Path
from typing import List, Dict, Any

import httpx

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "IstanbulVoices/0.1 (research prototype; contact ccolas@mit.edu)"
ISTANBUL_AREA_ID = 3600223474  # relation 223474 -> area

CACHE_DIR = Path(__file__).parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Cache schema version — bump when the element record shape or filter rules
# change so old caches are bypassed.
CACHE_VERSION = "v3"

# OSM tag keys we preserve per element.
KEEP_TAGS = {
    "name", "name:en", "name:tr",
    "alt_name", "old_name",
    "addr:street", "addr:housenumber", "addr:city",
    "addr:district", "addr:suburb", "addr:postcode", "addr:full",
    "website", "contact:website",
    "instagram", "contact:instagram",
    "facebook", "contact:facebook",
    "phone", "contact:phone",
    "operator", "operator:type", "brand",
    "opening_hours",
    "shop", "amenity", "tourism", "office", "leisure",
    "railway", "aeroway", "historic", "craft",
    "religion", "denomination", "cuisine",
    "wikidata", "wikipedia",
}

# Primary category tag. First present key wins.
CATEGORY_KEYS = [
    "shop", "amenity", "tourism", "office", "leisure",
    "railway", "aeroway", "historic", "craft",
]

# Lifecycle prefixes — elements with any such key are treated as not-currently-open.
LIFECYCLE_PREFIXES = (
    "disused:", "abandoned:", "demolished:", "destroyed:", "razed:",
    "removed:", "ruins:", "was:", "closed:", "proposed:", "planned:",
    "construction:",
)
# Tag values on primary keys that mean "not an active place of this type".
INACTIVE_VALUES = {"vacant", "closed", "disused", "abandoned", "no"}


def is_open(tags: Dict[str, str]) -> bool:
    """Heuristic: True if the element appears to be currently operating."""
    for k in tags:
        if any(k.startswith(p) for p in LIFECYCLE_PREFIXES):
            return False
    if tags.get("disused") not in (None, "", "no"):
        return False
    if tags.get("abandoned") not in (None, "", "no"):
        return False
    status = (tags.get("operational_status") or "").lower()
    if status in {"closed", "abandoned", "defunct", "demolished"}:
        return False
    for k in CATEGORY_KEYS:
        v = tags.get(k)
        if v and v.lower() in INACTIVE_VALUES:
            return False
    return True


def tags_cache_key(tags: List[Dict[str, Any]]) -> str:
    norm = sorted(
        (t["key"], t["value"], t.get("filter_key") or "", t.get("filter_value") or "")
        for t in tags
    )
    payload = {"v": CACHE_VERSION, "tags": norm}
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:16]


def build_query(tags: List[Dict[str, Any]], timeout: int = 180) -> str:
    parts = []
    for t in tags:
        k, v = t["key"], t["value"]
        extra = ""
        if t.get("filter_key") and t.get("filter_value"):
            extra = f'["{t["filter_key"]}"="{t["filter_value"]}"]'
        tag_sel = f'["{k}"]{extra}' if v == "*" else f'["{k}"="{v}"]{extra}'
        for elem in ("node", "way", "relation"):
            parts.append(f"  {elem}{tag_sel}(area.il);")
    body = "\n".join(parts)
    return (
        f"[out:json][timeout:{timeout}];\n"
        f"area({ISTANBUL_AREA_ID})->.il;\n"
        f"(\n{body}\n);\n"
        f"out center meta;\n"  # meta -> version, timestamp, user, changeset
    )


async def fetch_elements(tags: List[Dict[str, Any]], force: bool = False) -> List[Dict[str, Any]]:
    """Fetch named locations for the given tag set. Result is cached on disk."""
    key = tags_cache_key(tags)
    cache_file = CACHE_DIR / f"{key}.json.gz"
    if cache_file.exists() and not force:
        log.info("cache hit %s", cache_file.name)
        with gzip.open(cache_file, "rt", encoding="utf-8") as f:
            return json.load(f)

    query = build_query(tags)
    log.info("overpass query (%d tag specs)", len(tags))

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0), headers=headers) as client:
        last_err = None
        for attempt in range(4):
            try:
                r = await client.post(OVERPASS_URL, data={"data": query})
                if r.status_code in (429, 504):
                    wait = 10 * (attempt + 1)
                    log.warning("overpass %d, backing off %ds", r.status_code, wait)
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except httpx.HTTPError as e:
                last_err = e
                await asyncio.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(f"Overpass failed after retries: {last_err}")

    elements = []
    skipped_closed = 0
    for el in data.get("elements", []):
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
        kept_tags = {k: v for k, v in tagdict.items() if k in KEEP_TAGS}
        elements.append({
            "type": el["type"],
            "osm_id": el["id"],
            "id": f"{el['type']}/{el['id']}",
            "name": name,
            "lat": lat,
            "lon": lon,
            "timestamp": el.get("timestamp"),
            "tags": kept_tags,
        })
    log.info("skipped %d closed/disused elements", skipped_closed)

    with gzip.open(cache_file, "wt", encoding="utf-8") as f:
        json.dump(elements, f)
    log.info("cached %d elements to %s", len(elements), cache_file.name)
    return elements
