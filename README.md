# Namescape

*Geospatial linguistic landscape analysis from open POI sources.*

A small web app that shows, for a chosen set of location types (shops,
places of worship, government offices, …) and a list of keywords, the
**fraction of locations whose name matches any keyword** — mapped per
administrative district with a configurable color palette.

Ships as a **pure static site**: all queries happen at build time; the
deployed site is plain HTML/CSS/JS doing filter + matching + aggregation
in the browser.

## Data sources

The build can pull from up to three sources and merges them with spatial
+ name dedup (OSM has priority, then Foursquare, then Overture):

| source | how | auth | typical Istanbul size |
|---|---|---|---|
| **OpenStreetMap** | Overpass API | none | ~70k POIs |
| **Overture Maps Places** | DuckDB → S3 parquet | none (anonymous S3) | ~400k POIs |
| **Foursquare OS Places** | DuckDB → Hugging Face parquet | `HF_TOKEN` env var | ~varies |

After dedup, Istanbul lands at ~440k records.

## What it does

1. You pick a city.
2. You pick location types with checkboxes (hierarchical: category-level
   "all", individual OSM tags like `shop=bakery`, or whole external
   sources via the "External sources" group).
3. You type keywords (one per line).
4. You pick a matching mode:
   - **substring** — diacritic-insensitive substring match (fast, loose)
   - **root** — loose root matching against pre-computed Snowball stems;
     catches suffix variants in inflected languages (`kitap` → `kitabı`)
   - **regex** — JS-style regex, case-insensitive
5. You click Run. Filtering, matching, and per-district aggregation all
   run in the browser against the city's pre-built bundle.
6. The map recolors districts by the selected metric (fraction or total
   matches) using the chosen palette. You can grey out low-volume
   districts with a "min matches" threshold; districts with zero
   locations are dashed.
7. Click a district to see its matching names. Alt names that triggered
   a match are shown next to the primary name in italics.

## Layout

```
static/
├── cities.py          config: cities + polygon source + stemmer language
├── overpass.py        Overpass constants + is_open lifecycle filter
├── overture.py        Overture Maps fetcher (anonymous S3 parquet)
├── foursquare.py      Foursquare OS Places fetcher (HF_TOKEN required)
├── merge.py           cross-source spatial + name dedup
├── palettes.py        color stops per palette name
├── tags_config.py     the tag tree shown in the sidebar
├── build.py           orchestrator: fetch all sources → PIP → write bundle
└── site/              the deployable site
    ├── index.html  app.js  style.css
    └── data/          built by build.py (gzipped)
        ├── config.json         tag tree + palettes + city index
        └── <city>.json.gz      districts GeoJSON + POI records, one per city
requirements.txt
```

## Install

Python 3.10+:

```
pip install -r requirements.txt
```

## Build the data bundles

Default: pull from OSM + Overture (FSQ is included if `HF_TOKEN` is set).

```
python -m static.build               # build all cities
python -m static.build --only paris  # just one
python -m static.build --force       # ignore existing bundles
python -m static.build --no-overture # OSM only
python -m static.build --no-fsq      # OSM + Overture, no FSQ
```

To enable Foursquare:

1. Sign in at https://huggingface.co and accept the dataset terms at
   https://huggingface.co/datasets/foursquare/fsq-os-places.
2. Create a read token at https://huggingface.co/settings/tokens.
3. `export HF_TOKEN=hf_xxx` in the shell where you run the build.

A first-time city build hits Overpass (~30s), Overture S3 (~30–90s for
the bbox query + result transfer), and FSQ HF (~1–5min if enabled). Most
of the wall time on big cities is the parquet result transfer; the
DuckDB progress bar shows the query phase only.

The output is gzipped (`<city>.json.gz`) — ~25–30 MB for Istanbul. The
frontend decompresses it client-side via `DecompressionStream`.

## Serve / deploy

Locally:

```
python -m http.server 8000 --directory static/site
# open http://127.0.0.1:8000
```

For deployment, point GitHub Pages (or any static host: Cloudflare Pages,
Netlify, S3+CloudFront, …) at **`static/site/`** (and only that
subdirectory — the rest of `static/` is build-only Python). No build
step on the host.

See `static/README.md` for more on the build, supported cities, and
matching modes.

## Author

Built by [Cédric Colas](https://cedriccolas.com). POI data from
[OpenStreetMap](https://www.openstreetmap.org), [Overture Maps](https://overturemaps.org),
and [Foursquare OS Places](https://opensource.foursquare.com/os-places/).
Polygon sources: OSM `admin_level` relations for most cities;
[Chicago Open Data](https://data.cityofchicago.org) for Chicago Community
Areas.
