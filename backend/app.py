"""FastAPI app: serves frontend + query API."""
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import districts, matching, overpass
from .overpass import CATEGORY_KEYS
from .palettes import PALETTES
from .tags_config import TAG_CATEGORIES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("namescape")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="Namescape")


class TagSpec(BaseModel):
    key: str
    value: str
    filter_key: Optional[str] = None
    filter_value: Optional[str] = None
    label: Optional[str] = None


class QueryRequest(BaseModel):
    tags: List[TagSpec] = Field(..., min_length=1)
    keywords: List[str] = Field(default_factory=list)
    mode: str = "stemmed"


def _build_spec_index(specs: List[dict]) -> List[dict]:
    """Return specs sorted so most-specific (with filter) matches first."""
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


def _category_label(tags: dict, ordered_specs: List[dict]) -> str:
    """Prefer a user-facing label from the matched spec; else fall back to raw key=value."""
    for spec in ordered_specs:
        if _spec_matches(spec, tags):
            return spec.get("label") or f"{spec['key']}={spec['value']}"
    for k in CATEGORY_KEYS:
        v = tags.get(k)
        if v:
            return f"{k}={v}"
    return "other"


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


def _enrich(el: dict, ordered_specs: List[dict]) -> dict:
    t = el.get("tags") or {}
    return {
        "name": el["name"],
        "lat": el["lat"],
        "lon": el["lon"],
        "id": el["id"],
        "type": el["type"],
        "osm_id": el["osm_id"],
        "timestamp": el.get("timestamp"),
        "category": _category_label(t, ordered_specs),
        "address": _format_address(t),
        "website": _first(t, ["website", "contact:website"]),
        "instagram": _first(t, ["instagram", "contact:instagram"]),
        "operator_type": t.get("operator:type", ""),
        "operator": t.get("operator", ""),
        "old_name": t.get("old_name", ""),
        "alt_name": t.get("alt_name", ""),
    }


@app.on_event("startup")
def _warm_districts() -> None:
    try:
        districts.load()
        log.info("districts loaded: %d", len(districts.all_names()))
    except FileNotFoundError as e:
        log.warning(str(e))


@app.get("/api/tags")
def get_tags():
    return TAG_CATEGORIES


@app.get("/api/palettes")
def get_palettes():
    return PALETTES


@app.get("/api/districts")
def get_districts():
    try:
        _, _, _, gj = districts.load()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return JSONResponse(gj)


@app.post("/api/query")
async def run_query(req: QueryRequest):
    if req.mode not in ("substring", "stemmed", "regex"):
        raise HTTPException(400, f"bad mode: {req.mode}")

    tag_dicts = [t.model_dump() for t in req.tags]
    log.info("query: %d tag specs, %d keywords, mode=%s",
             len(tag_dicts), len(req.keywords), req.mode)

    try:
        elements = await overpass.fetch_elements(tag_dicts)
    except Exception as e:
        log.exception("overpass failed")
        raise HTTPException(502, f"Overpass error: {e}")

    ordered_specs = _build_spec_index(tag_dicts)

    per_district: dict = {name: {
        "count": 0, "matches": 0,
        "examples": [], "match_examples": [],
    } for name in districts.all_names()}

    unassigned = 0
    for el in elements:
        d = districts.assign(el["lat"], el["lon"])
        if d is None:
            unassigned += 1
            continue
        stats = per_district.setdefault(d, {
            "count": 0, "matches": 0, "examples": [], "match_examples": [],
        })
        stats["count"] += 1
        if len(stats["examples"]) < 10:
            stats["examples"].append(el["name"])
        if matching.match(el["name"], req.keywords, req.mode):
            stats["matches"] += 1
            stats["match_examples"].append(_enrich(el, ordered_specs))

    for stats in per_district.values():
        stats["fraction"] = (stats["matches"] / stats["count"]) if stats["count"] else 0.0

    return {
        "per_district": per_district,
        "total_elements": len(elements),
        "unassigned": unassigned,
    }


# Static frontend (mounted last so it doesn't shadow /api routes)
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
