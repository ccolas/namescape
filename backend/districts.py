"""Istanbul district polygons and point-in-polygon lookup."""
import json
from pathlib import Path
from typing import Optional, Tuple

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

GEOJSON_PATH = Path(__file__).parent / "data" / "istanbul_districts.geojson"

_cache: Optional[Tuple[list, list, STRtree, dict]] = None


def load() -> Tuple[list, list, STRtree, dict]:
    """Return (geoms, names, STRtree, geojson_dict). Cached after first call."""
    global _cache
    if _cache is not None:
        return _cache
    if not GEOJSON_PATH.exists():
        raise FileNotFoundError(
            f"{GEOJSON_PATH} is missing. Run: python -m backend.setup_districts"
        )
    with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
        gj = json.load(f)
    geoms = [shape(feat["geometry"]) for feat in gj["features"]]
    names = [feat["properties"]["name"] for feat in gj["features"]]
    tree = STRtree(geoms)
    _cache = (geoms, names, tree, gj)
    return _cache


def assign(lat: float, lon: float) -> Optional[str]:
    geoms, names, tree, _ = load()
    pt = Point(lon, lat)
    # shapely 2.0: STRtree.query returns indices
    for idx in tree.query(pt):
        if geoms[int(idx)].contains(pt):
            return names[int(idx)]
    return None


def all_names() -> list:
    _, names, _, _ = load()
    return list(names)
