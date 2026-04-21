"""One-shot audit: for every tag spec in tags_config, print its count in
Fatih district and total across all Istanbul districts. Prints a table and
highlights specs with 0 locations (candidates for removal).

Uses ONE Overpass query spanning all category keys, then attributes each
element in Python.

Run:  python -m backend.audit_tags
"""
import asyncio
import sys
from collections import Counter

from .districts import assign, load
from .overpass import (
    CATEGORY_KEYS, KEEP_TAGS, OVERPASS_URL, USER_AGENT, ISTANBUL_AREA_ID,
    is_open,
)
from .tags_config import TAG_CATEGORIES

import httpx


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


async def fetch_all() -> list:
    # Collect all distinct (key,) that appear in tags_config; use key-only
    # selectors to catch every element that could match any spec.
    keys = set()
    extra_selectors = []
    for cat in TAG_CATEGORIES:
        for t in cat["tags"]:
            keys.add(t["key"])
            # For filter-based specs (e.g. religion=muslim) the base key is
            # already included; religion filter is read from element tags.

    parts = []
    for k in sorted(keys):
        for elem in ("node", "way", "relation"):
            parts.append(f'  {elem}["{k}"](area.il);')
    body = "\n".join(parts)
    query = (
        f"[out:json][timeout:300];\n"
        f"area({ISTANBUL_AREA_ID})->.il;\n"
        f"(\n{body}\n);\n"
        f"out center meta;\n"
    )

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0), headers=headers) as c:
        r = await c.post(OVERPASS_URL, data={"data": query})
        r.raise_for_status()
        return r.json().get("elements", [])


def main() -> None:
    load()  # sanity-check districts exist
    print("Fetching all relevant OSM elements from Overpass (1 query, ~60–120s)...", flush=True)
    elements = asyncio.run(fetch_all())
    print(f"Got {len(elements)} raw elements.", flush=True)

    # Filter: has name, is open, has lat/lon.
    cleaned = []
    for el in elements:
        tagdict = el.get("tags") or {}
        name = tagdict.get("name") or tagdict.get("name:tr") or tagdict.get("name:en")
        if not name or not is_open(tagdict):
            continue
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        cleaned.append((tagdict, lat, lon))
    print(f"After filtering (named, open, geocoded): {len(cleaned)}", flush=True)

    # Attribute to districts
    elements_with_district = [
        (tagdict, assign(lat, lon)) for tagdict, lat, lon in cleaned
    ]

    # Per-spec totals
    rows = []
    for cat in TAG_CATEGORIES:
        for t in cat["tags"]:
            total = 0
            fatih = 0
            for tagdict, district in elements_with_district:
                if _spec_matches(t, tagdict):
                    total += 1
                    if district == "Fatih":
                        fatih += 1
            rows.append((cat["label"], t["label"], t, fatih, total))

    # Print table
    print()
    print(f"{'Category':<24} {'Tag':<32} {'Fatih':>7} {'Total':>8}")
    print("-" * 75)
    last_cat = None
    for cat_label, tag_label, _spec, fatih, total in rows:
        flag = "  ← 0 in Fatih" if fatih == 0 else ""
        if cat_label != last_cat:
            print()
            last_cat = cat_label
        print(f"{cat_label:<24} {tag_label:<32} {fatih:>7} {total:>8}{flag}")

    zero = [(cl, tl, spec) for cl, tl, spec, f, _t in rows if f == 0]
    print()
    print(f"Specs with 0 locations in Fatih: {len(zero)}")
    for cl, tl, spec in zero:
        extra = ""
        if spec.get("filter_key"):
            extra = f" [+ {spec['filter_key']}={spec['filter_value']}]"
        print(f"  - {cl} / {tl}  ({spec['key']}={spec['value']}){extra}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
