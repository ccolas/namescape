"""Fetch POIs from Foursquare Open Source Places for a bbox.

The dataset is gated on Hugging Face (free, but requires agreeing to terms
and generating a token). Set HF_TOKEN to enable this source; otherwise
fetch() returns an empty list and logs a skip.

  1. https://huggingface.co/datasets/foursquare/fsq-os-places — click
     "Agree and access repository".
  2. https://huggingface.co/settings/tokens — create a read token.
  3. export HF_TOKEN=hf_xxx
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Tuple

log = logging.getLogger(__name__)

FSQ_RELEASE = "2026-04-14"
# DuckDB has a first-class hf:// protocol that handles auth via a HUGGINGFACE
# secret. This is the supported path for gated datasets.
FSQ_HF_PARQUET = (
    f"hf://datasets/foursquare/fsq-os-places/release/dt={FSQ_RELEASE}"
    f"/places/parquet/*.parquet"
)

_EXT_DIR = Path(__file__).resolve().parent.parent / ".duckdb_ext"


def _connect(token: str):
    import duckdb
    _EXT_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(config={"extension_directory": str(_EXT_DIR)})
    con.execute("INSTALL httpfs; LOAD httpfs;")
    # Use DuckDB's HUGGINGFACE secret type so the bearer token is added to
    # every hf:// request automatically.
    con.execute(
        "CREATE OR REPLACE SECRET hf_fsq (TYPE HUGGINGFACE, TOKEN ?);",
        [token],
    )
    con.execute("SET enable_progress_bar=true;")
    con.execute("SET progress_bar_time=1000;")
    return con


def fetch(bbox: Tuple[float, float, float, float]) -> List[dict]:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        log.info("  Foursquare: skipped (HF_TOKEN not set). See static/foursquare.py docstring.")
        return []

    s, w, n, e = bbox
    con = _connect(token)
    log.info("  Foursquare: querying places in bbox (%.3f,%.3f,%.3f,%.3f)…", s, w, n, e)
    q = f"""
    SELECT fsq_place_id,
           name,
           latitude               AS lat,
           longitude              AS lon,
           fsq_category_labels    AS categories,   -- list<varchar>
           address,
           locality,
           region,
           date_closed
    FROM read_parquet('{FSQ_HF_PARQUET}')
    WHERE latitude  BETWEEN {s} AND {n}
      AND longitude BETWEEN {w} AND {e}
      AND name IS NOT NULL
      AND date_closed IS NULL
    """
    import time
    t0 = time.time()
    log.info("  Foursquare: query running (DuckDB progress bar above)…")
    reader = con.execute(q).fetch_record_batch(rows_per_batch=50_000)
    log.info("  Foursquare: query done in %.1fs; reading row batches…", time.time() - t0)

    out = []
    last_log = time.time()
    for batch in reader:
        for r in batch.to_pylist():
            cats = r.get("categories")
            # Keep the full hierarchy ("Retail > Furniture and Home Store") so
            # the category mapper can fall back to the parent when the leaf
            # has no rule. category_map tries the full string then each
            # `>`-separated segment.
            category = cats[0] if cats else ""
            out.append({
                "source": "fsq",
                "external_id": r["fsq_place_id"],
                "name": r["name"],
                "alt_names": [],
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "category": category,
                "brand": "",
                "address": r.get("address") or "",
            })
        if time.time() - last_log > 5:
            log.info("  Foursquare: …processed %d rows so far", len(out))
            last_log = time.time()
    log.info("  Foursquare: %d open places (%.1fs total)", len(out), time.time() - t0)
    return out
