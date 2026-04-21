"""One-time fetch of Istanbul ilce (district) polygons from Overpass.

Run as:  python -m backend.setup_districts
"""
import json
import sys
from pathlib import Path

import httpx
import osm2geojson

from .overpass import OVERPASS_URL, ISTANBUL_AREA_ID

OUT_PATH = Path(__file__).parent / "data" / "istanbul_districts.geojson"

QUERY = f"""
[out:json][timeout:300];
area({ISTANBUL_AREA_ID})->.il;
relation["admin_level"="6"]["boundary"="administrative"](area.il);
out body;
>;
out skel qt;
""".strip()


USER_AGENT = "IstanbulVoices/0.1 (research prototype; contact ccolas@mit.edu)"


def fetch() -> None:
    print(f"Querying Overpass for Istanbul districts...", flush=True)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0), headers=headers) as client:
        r = client.post(OVERPASS_URL, data={"data": QUERY})
        r.raise_for_status()
        raw = r.json()

    print(f"Converting {len(raw.get('elements', []))} OSM elements to GeoJSON...", flush=True)
    gj = osm2geojson.json2geojson(raw)

    features = []
    seen = set()
    for feat in gj.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        props = feat.get("properties", {}) or {}
        tags = props.get("tags", props) or {}
        name = tags.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        features.append({
            "type": "Feature",
            "properties": {
                "name": name,
                "name_en": tags.get("name:en"),
                "population": tags.get("population"),
            },
            "geometry": geom,
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)
    print(f"Wrote {len(features)} districts to {OUT_PATH}", flush=True)


if __name__ == "__main__":
    try:
        fetch()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
