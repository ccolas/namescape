"""Audit the city bundle for likely-missed duplicates.

Scans every pair of records that are spatially close-ish AND share a
distinctive name token, but that the build-time dedup left in. Outputs
the worst offenders so we can see *why* they slipped through (too far
apart? names too dissimilar? cross-source coordinate drift?).

Usage:
    python -m static.audit_dups istanbul
    python -m static.audit_dups istanbul --radius 500 --top 60
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

BUNDLE_DIR = Path(__file__).resolve().parent / "site" / "data"

# Same Turkish-aware token normalizer as merge.py uses, kept independent
# so this script can evolve without breaking the live merger.
_TR_MAP = str.maketrans({
    "ş": "s", "Ş": "s", "ı": "i", "İ": "i", "ğ": "g", "Ğ": "g",
    "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
})
_TOKEN_RE = re.compile(r"[A-Za-z0-9À-ɏͰ-ϿЀ-ӿ]+")
_STOP = {"the", "and", "of", "de", "la", "le", "el", "ve", "ile",
         "cafe", "restaurant", "shop", "store", "magaza", "magazasi"}


def norm_tokens(text: str) -> set:
    if not text:
        return set()
    t = text.translate(_TR_MAP).lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {tok[:5] if len(tok) > 5 else tok
            for tok in _TOKEN_RE.findall(t) if tok not in _STOP}


def all_tokens(rec: dict) -> set:
    out = norm_tokens(rec.get("n", ""))
    if rec.get("ns"):
        for part in rec["ns"].split(" | "):
            out |= norm_tokens(part)
    return out


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def name_similarity(a: set, b: set) -> tuple[float, float]:
    """Return (jaccard, overlap_coefficient). Both 0 if either is empty."""
    if not a or not b:
        return 0.0, 0.0
    inter = len(a & b)
    union = len(a | b)
    smaller = min(len(a), len(b))
    return (inter / union if union else 0.0,
            inter / smaller if smaller else 0.0)


def load_bundle(city: str) -> dict:
    path = BUNDLE_DIR / f"{city}.json.gz"
    if not path.exists():
        path = BUNDLE_DIR / f"{city}.json"
    if not path.exists():
        sys.exit(f"bundle not found: {city}")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("city")
    ap.add_argument("--radius", type=float, default=500.0,
                    help="pair distance ceiling in meters (default 500)")
    ap.add_argument("--top", type=int, default=40,
                    help="how many candidate dup pairs to print (default 40)")
    ap.add_argument("--min-jaccard", type=float, default=0.34)
    ap.add_argument("--min-overlap", type=float, default=0.7)
    ap.add_argument("--district", default=None,
                    help="limit to a single district (e.g. Fatih)")
    args = ap.parse_args()

    bundle = load_bundle(args.city)
    elements = bundle["elements"]
    print(f"loaded {len(elements):,} records from {args.city}")

    # Cheap lat/lon grid for the spatial join. Cell size ~250m at mid-lat.
    cell_lat = 250 / 111000.0
    cell_lon = 250 / 84000.0
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)

    # Pre-compute tokens once per record. Skip empty-name records.
    toks_list: list[set] = []
    keep_indices: list[int] = []
    for i, el in enumerate(elements):
        if args.district and el.get("d") != args.district:
            toks_list.append(set())
            continue
        toks = all_tokens(el)
        toks_list.append(toks)
        if not toks:
            continue
        i_lat = int(el["lat"] / cell_lat)
        j_lon = int(el["lon"] / cell_lon)
        # Span enough neighbor cells that we cover --radius m on each side.
        grid[(i_lat, j_lon)].append(i)
        keep_indices.append(i)

    span = max(1, int(math.ceil(args.radius / 250)))
    print(f"grid: {len(grid)} cells · scanning ±{span} cells per record")

    pairs = []
    seen_pair = set()
    for i in keep_indices:
        a = elements[i]
        ta = toks_list[i]
        if not ta:
            continue
        i_lat = int(a["lat"] / cell_lat)
        j_lon = int(a["lon"] / cell_lon)
        for di in range(-span, span + 1):
            for dj in range(-span, span + 1):
                for k in grid.get((i_lat + di, j_lon + dj), ()):
                    if k <= i:
                        continue
                    b = elements[k]
                    if a.get("d") != b.get("d"):
                        # focus on within-district duplicates first; cross-
                        # district matches are usually the bad-coord case
                        # and we'll surface them in a second pass.
                        continue
                    tb = toks_list[k]
                    if not tb:
                        continue
                    jac, ovl = name_similarity(ta, tb)
                    if jac < args.min_jaccard and ovl < args.min_overlap:
                        continue
                    d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
                    if d > args.radius:
                        continue
                    pairs.append((jac, ovl, d, i, k))

    pairs.sort(key=lambda p: (-p[0], p[2]))
    print(f"\n{len(pairs)} candidate within-district pairs (jaccard>={args.min_jaccard} or overlap>={args.min_overlap}, dist<={args.radius}m)\n")

    shown = 0
    for jac, ovl, d, i, k in pairs:
        if shown >= args.top:
            break
        a, b = elements[i], elements[k]
        print(f"  jac={jac:.2f} ovl={ovl:.2f} dist={d:5.0f}m district={a.get('d','?')}")
        print(f"    [{a.get('src','?')}] {a['n']!r} cat={a.get('c','')!r} alt={a.get('ns','')!r} src2={a.get('src2','')}")
        print(f"    [{b.get('src','?')}] {b['n']!r} cat={b.get('c','')!r} alt={b.get('ns','')!r} src2={b.get('src2','')}")
        print(f"    coords: ({a['lat']:.6f}, {a['lon']:.6f})  vs  ({b['lat']:.6f}, {b['lon']:.6f})")
        print()
        shown += 1

    # ---- second pass: cross-district likely-dups (bad coordinates) ----
    print("\n=== cross-district candidates (likely bad coordinates) ===\n")
    cross = []
    for i in keep_indices:
        a = elements[i]
        ta = toks_list[i]
        if not ta:
            continue
        i_lat = int(a["lat"] / cell_lat)
        j_lon = int(a["lon"] / cell_lon)
        for di in range(-span, span + 1):
            for dj in range(-span, span + 1):
                for k in grid.get((i_lat + di, j_lon + dj), ()):
                    if k <= i:
                        continue
                    b = elements[k]
                    if a.get("d") == b.get("d"):
                        continue
                    tb = toks_list[k]
                    if not tb:
                        continue
                    jac, ovl = name_similarity(ta, tb)
                    if jac < 0.5 and ovl < 0.85:
                        continue
                    d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
                    if d > args.radius:
                        continue
                    cross.append((jac, ovl, d, i, k))

    cross.sort(key=lambda p: (-p[0], p[2]))
    for jac, ovl, d, i, k in cross[:20]:
        a, b = elements[i], elements[k]
        print(f"  jac={jac:.2f} ovl={ovl:.2f} dist={d:5.0f}m  {a.get('d','?')!r} ↔ {b.get('d','?')!r}")
        print(f"    [{a.get('src','?')}] {a['n']!r}  ({a['lat']:.6f}, {a['lon']:.6f})")
        print(f"    [{b.get('src','?')}] {b['n']!r}  ({b['lat']:.6f}, {b['lon']:.6f})")
        print()

    # ---- summary by source pair ----
    print("=== same-district pair count by (src_a, src_b) ===")
    by_srcs = defaultdict(int)
    for jac, ovl, d, i, k in pairs:
        sa = elements[i].get("src", "?")
        sb = elements[k].get("src", "?")
        key = tuple(sorted((sa, sb)))
        by_srcs[key] += 1
    for k, n in sorted(by_srcs.items(), key=lambda x: -x[1]):
        print(f"  {k}: {n}")


if __name__ == "__main__":
    main()
