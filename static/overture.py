"""Fetch POIs from Overture Maps (places theme) for a bbox.

Queries the public S3 parquet dataset anonymously via DuckDB + httpfs.
Returns a list of normalized records.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Tuple

log = logging.getLogger(__name__)

OVERTURE_RELEASE = "2026-04-15.0"
OVERTURE_PATH = (
    f"s3://overturemaps-us-west-2/release/{OVERTURE_RELEASE}"
    "/theme=places/type=place/*"
)

# Project-local extension dir so we don't fight sandboxed ~/.duckdb perms.
_EXT_DIR = Path(__file__).resolve().parent.parent / ".duckdb_ext"


def _connect():
    import duckdb
    _EXT_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(config={"extension_directory": str(_EXT_DIR)})
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    # Anonymous S3: empty credentials unlock public buckets.
    con.execute(
        "CREATE OR REPLACE SECRET overture (TYPE S3, KEY_ID '', SECRET '', "
        "REGION 'us-west-2', SCOPE 's3://overturemaps-us-west-2');"
    )
    # Progress bar to stderr so the user sees query progress.
    con.execute("SET enable_progress_bar=true;")
    con.execute("SET progress_bar_time=1000;")  # show after 1s
    return con


def fetch(bbox: Tuple[float, float, float, float]) -> List[dict]:
    """Return records: {external_id, name, alt_names, lat, lon, category, brand}.

    bbox = (south, west, north, east) — same convention as Overpass.
    """
    import time
    s, w, n, e = bbox
    con = _connect()
    log.info("  Overture: querying places in bbox (%.3f,%.3f,%.3f,%.3f)…", s, w, n, e)
    q = f"""
    SELECT id,
           names.primary            AS name,
           names.common             AS common_names,
           names.rules              AS rule_names,
           categories.primary       AS category,
           brand.names.primary      AS brand,
           bbox.xmin                AS lon,
           bbox.ymin                AS lat,
           addresses                AS addresses
    FROM read_parquet('{OVERTURE_PATH}')
    WHERE bbox.xmin BETWEEN {w} AND {e}
      AND bbox.ymin BETWEEN {s} AND {n}
      AND names.primary IS NOT NULL
    """
    t0 = time.time()
    # Stream via Arrow + record batches — much faster than fetchall() for
    # nested structs, and lets us log progress as batches arrive.
    log.info("  Overture: query running (DuckDB progress bar above)…")
    reader = con.execute(q).fetch_record_batch(rows_per_batch=50_000)
    log.info("  Overture: query done in %.1fs; reading row batches…", time.time() - t0)

    out = []
    batch_n = 0
    last_log = time.time()
    for batch in reader:
        batch_n += 1
        rows = batch.to_pylist()
        for r in rows:
            name = r["name"]
            alt_names = []
            seen = {name}
            for arr_key in ("common_names", "rule_names"):
                arr = r.get(arr_key)
                if not arr:
                    continue
                for item in arr:
                    v = item.get("value") if isinstance(item, dict) else None
                    if v and v not in seen:
                        alt_names.append(v)
                        seen.add(v)
            brand = r.get("brand")
            if brand and brand not in seen:
                alt_names.append(brand)
            address = ""
            addrs = r.get("addresses")
            if addrs:
                first = addrs[0] if isinstance(addrs, list) else None
                if isinstance(first, dict):
                    address = first.get("freeform") or ""
            out.append({
                "source": "overture",
                "external_id": r["id"],
                "name": name,
                "alt_names": alt_names,
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "category": r.get("category") or "",
                "brand": brand or "",
                "address": address,
            })
        # Heartbeat every ~5s.
        if time.time() - last_log > 5:
            log.info("  Overture: …processed %d rows so far", len(out))
            last_log = time.time()
    log.info("  Overture: %d places after name filter (%.1fs total)", len(out), time.time() - t0)
    return out
