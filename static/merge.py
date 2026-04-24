"""Merge POI records from multiple sources with spatial + name dedup.

Strategy:
  - Walk records in priority order (OSM first — richest local naming,
    then Foursquare, then Overture).
  - Bucket into a ~100m grid; for each incoming record check the 3×3
    cells of its bucket for existing records within `distance_m`.
  - If a prior record is spatially close AND name-similar, treat as
    duplicate: merge the new names into the prior record's alt_names and
    drop the duplicate.
  - Otherwise keep.
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from typing import Dict, List, Tuple

log = logging.getLogger(__name__)

# Rough degrees-per-meter at mid-latitude (~41°N works for most of our cities).
# We only use these for the coarse grid buckets and the cheap box prefilter;
# the final distance check is haversine.
_DEG_PER_M_LAT = 1 / 111000.0
_DEG_PER_M_LON = 1 / 84000.0

_TR_MAP = str.maketrans({
    "ş": "s", "Ş": "s", "ı": "i", "İ": "i", "ğ": "g", "Ğ": "g",
    "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
})
_TOKEN_RE = re.compile(r"[A-Za-z0-9À-ɏͰ-ϿЀ-ӿ]+")
_STOPWORDS = {
    "the", "and", "of", "de", "la", "le", "el",
    "ve", "ile",  # Turkish
}


def _stem_prefix(tok: str) -> str:
    """Cheap stand-in for a real stemmer — keep just the first 5 chars.

    Catches Turkish suffix variants the dedup would otherwise miss
    (kuyumcu / kuyumculuk / kuyumcular all → 'kuyum'), without taking
    a Snowball dependency in merge.py. Short tokens (<5 chars) pass
    through unchanged.
    """
    return tok[:5] if len(tok) > 5 else tok


def _normalize_tokens(text: str) -> set:
    if not text:
        return set()
    t = text.translate(_TR_MAP).lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    toks = {_stem_prefix(tok) for tok in _TOKEN_RE.findall(t) if tok not in _STOPWORDS}
    return toks


def _record_tokens(rec: dict) -> set:
    """Token set across the record's primary name + alt names."""
    toks = _normalize_tokens(rec.get("name", ""))
    for alt in rec.get("alt_names") or []:
        toks |= _normalize_tokens(alt)
    return toks


def _names_match(a_tokens: set, b_tokens: set) -> bool:
    if not a_tokens or not b_tokens:
        return False
    smaller, larger = (a_tokens, b_tokens) if len(a_tokens) <= len(b_tokens) else (b_tokens, a_tokens)
    # Fast path: full subset of small into large (e.g. "Starbucks" ⊆ "Starbucks Coffee").
    if smaller.issubset(larger):
        return True
    inter = len(smaller & larger)
    union = len(smaller | larger)
    if not union:
        return False
    # Overlap-coefficient fallback: intersection / smaller. Catches cases
    # where one name carries extra filler tokens the other doesn't
    # ("Fetih Mah. Buhara Parki" vs "Fetih Mahallesi Buhara Parkı İlçe Atasehir").
    # Plus a looser Jaccard floor for short-name cases.
    jaccard = inter / union
    overlap = inter / len(smaller)
    return jaccard >= 0.4 or (overlap >= 0.75 and inter >= 2)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _merge_alt_names(kept: dict, dup: dict) -> None:
    existing = {kept.get("name") or ""} | set(kept.get("alt_names") or [])
    to_add = []
    for candidate in [dup.get("name")] + list(dup.get("alt_names") or []):
        if candidate and candidate not in existing:
            to_add.append(candidate)
            existing.add(candidate)
    if to_add:
        kept.setdefault("alt_names", []).extend(to_add)
    # Track alternate sources so the record retains provenance.
    kept.setdefault("also_in", []).append(dup.get("source", "?"))


def merge(records_by_source: Dict[str, List[dict]],
          priority: Tuple[str, ...] = ("osm", "fsq", "overture"),
          distance_m: float = 100.0) -> List[dict]:
    """Return a deduped list. Earlier-priority sources win."""
    cell_lat = 100.0 * _DEG_PER_M_LAT  # 100m cell
    cell_lon = 100.0 * _DEG_PER_M_LON
    buf_lat = distance_m * _DEG_PER_M_LAT * 1.2  # slight over-estimate for cheap box prefilter
    buf_lon = distance_m * _DEG_PER_M_LON * 1.2

    grid: Dict[Tuple[int, int], List[int]] = {}
    kept: List[dict] = []
    kept_tokens: List[set] = []

    counts = {"kept": 0, "merged": 0}
    per_source = {s: {"in": 0, "kept": 0, "merged": 0} for s in records_by_source}

    for source in priority:
        recs = records_by_source.get(source) or []
        per_source[source]["in"] = len(recs)
        for rec in recs:
            lat, lon = rec["lat"], rec["lon"]
            i = int(lat / cell_lat)
            j = int(lon / cell_lon)
            toks = _record_tokens(rec)

            dup_idx = -1
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for idx in grid.get((i + di, j + dj), ()):
                        other = kept[idx]
                        # cheap bbox check first
                        if abs(other["lat"] - lat) > buf_lat or abs(other["lon"] - lon) > buf_lon:
                            continue
                        if _haversine_m(lat, lon, other["lat"], other["lon"]) > distance_m:
                            continue
                        if _names_match(toks, kept_tokens[idx]):
                            dup_idx = idx
                            break
                    if dup_idx >= 0:
                        break
                if dup_idx >= 0:
                    break

            if dup_idx >= 0:
                _merge_alt_names(kept[dup_idx], rec)
                # Re-index merged tokens for future matches.
                kept_tokens[dup_idx] |= toks
                counts["merged"] += 1
                per_source[source]["merged"] += 1
            else:
                grid.setdefault((i, j), []).append(len(kept))
                kept.append(rec)
                kept_tokens.append(toks)
                counts["kept"] += 1
                per_source[source]["kept"] += 1

    log.info("  merge: %d kept, %d merged", counts["kept"], counts["merged"])
    for s, c in per_source.items():
        log.info("    %s: %d in -> %d kept, %d merged into prior", s, c["in"], c["kept"], c["merged"])
    return kept
