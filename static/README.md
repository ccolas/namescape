# Namescape — build pipeline

Pure HTML/CSS/JS version of Namescape that runs entirely in the browser.
All POI queries happen at **build time**. The deployed site just ships
pre-computed per-city gzipped JSON bundles and does filter + matching +
aggregation in-memory.

## What's here

```
static/
├── cities.py          config: cities + polygon source + stemmer language
├── overpass.py        Overpass URL/UA + the is_open lifecycle filter
├── overture.py        Overture Maps fetcher (anonymous S3 parquet via DuckDB)
├── foursquare.py      Foursquare OS Places fetcher (HF_TOKEN required)
├── merge.py           cross-source spatial + name dedup
├── palettes.py        color stops per palette name
├── tags_config.py     the tag tree shown in the sidebar
├── build.py           orchestrator: districts → POIs (3 sources) → merge → bundle
└── site/              the deployable site
    ├── index.html  app.js  style.css
    └── data/
        ├── config.json         tag tree + palettes + city index
        └── <city>.json.gz      districts GeoJSON + POI records (one per city)
```

## Cities

| City | Subdivision | Polygon source | Stemmer |
|---|---|---|---|
| Istanbul | ilçe | OSM admin_level=6 | turkish |
| Paris | arrondissement | OSM admin_level=9 | french |
| Berlin | Bezirk | OSM admin_level=9 | german |
| London | borough | OSM admin_level=8 | english |
| Madrid | distrito | OSM admin_level=9 | spanish |
| Chicago | community area | Chicago Open Data portal | english |

To add a city: append an entry to `static/cities.py`, then
`python -m static.build --only <new_id>`.

## Sources & dedup

By default the build pulls from three POI sources for each city's bbox:

1. **OpenStreetMap** via Overpass API (no auth)
2. **Overture Maps Places** via DuckDB → public S3 parquet (no auth)
3. **Foursquare OS Places** via DuckDB → Hugging Face parquet (gated:
   `HF_TOKEN` env var required, dataset terms must be accepted at
   https://huggingface.co/datasets/foursquare/fsq-os-places)

`merge.py` then deduplicates spatially + by name across sources. Within
50 m, two records are treated as the same place if (a) the smaller name
token set is a subset of the larger, or (b) Jaccard similarity ≥ 0.5.
Source priority on conflict: **OSM → FSQ → Overture** (OSM names are
richest locally; FSQ is the freshest commercial source).

The merged record keeps the higher-priority source's display name and
appends the others' names to its `alt_names`. The frontend matcher
searches across primary + alt names, so a "Denizbank" branch tagged
"Denizbank Fetih Şubesi" in one source will hit a "fetih" search.

## Build

```
# in an env with deps installed
python -m static.build               # build all cities (OSM + Overture + FSQ if HF_TOKEN)
python -m static.build --only paris  # just one
python -m static.build --force       # ignore existing bundles
python -m static.build --sources osm           # OSM only
python -m static.build --sources osm,overture  # OSM + Overture, no FSQ
```

A city bundle (`data/<city>.json.gz`) is only rewritten when missing or
when `--force` is passed. `data/config.json` is rewritten every run.

Approximate timing per city (Istanbul-sized): Overpass ~30 s, Overture
~30–90 s (S3 query + transfer), Foursquare ~1–5 min (HF is slow), merge
~10 s, write ~5 s. Watch the DuckDB progress bar during the parquet
queries.

## Run locally

```
python -m http.server 8000 --directory site
# open http://127.0.0.1:8000
```

Leaflet + Inter + CartoDB tiles load from CDNs; everything else is local.

## Deploy to GitHub Pages

Push the contents of **`static/site/`** (and only that subdirectory — the
build code, parent README, etc. are not needed at runtime) to either:

- **The `/docs` folder on main** — rename or symlink `static/site` → `docs/`.
- **A `gh-pages` branch** — push `static/site/` contents.

Or push `static/site/` to any static host (Cloudflare Pages, Netlify,
S3+CloudFront, …). No build step required.

## Matching modes

Three modes ship with each bundle; "root" uses pre-computed Snowball
stems written at build time, so the JS runtime doesn't need a stemmer.

- **substring** — diacritic-insensitive substring on the full name.
- **root** — loose root-word matching against pre-stemmed tokens. Catches
  suffix variants in inflected languages (kitap → kitapçı, kitaplar,
  kitabı). Implemented as bidirectional 4-char prefix + substring match
  on stems.
- **regex** — JS regex, case-insensitive, applied to the original name
  (with diacritics preserved).

All three search across primary + alt names of each merged record.

## Refreshing data

POI sources change all the time. Re-run the build monthly or whenever
you want fresh data, then commit the new `<city>.json.gz` files and
redeploy. No runtime POI fetches are ever caused by your visitors.

## Author

[Cédric Colas](https://cedriccolas.com)
