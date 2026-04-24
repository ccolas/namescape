"""Constants and helpers shared by the static build's Overpass calls."""
from typing import Dict


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "Namescape/0.1 (research prototype; contact ccolas@mit.edu)"

# OSM tag keys we preserve per element.
KEEP_TAGS = {
    "name", "name:en", "name:tr",
    "alt_name", "old_name", "short_name", "loc_name", "official_name",
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

# Name fields searched across (in order of display preference).
NAME_KEYS = [
    "name", "name:en", "name:tr",
    "alt_name", "old_name", "short_name", "loc_name", "official_name",
    "brand",
]

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
