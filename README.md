# Namescape

*Where do words live on the map?*

Namescape is a small web app that, for a chosen city and a list of keywords, shows **the share of named places whose name contains those keywords** — broken down per administrative district and place types, mapped with a configurable color palette. Running at: https://ccolas.github.io/namescape/

## What it's for

Place names carry signal. Streets, shops, mosques, parks, government offices — the words people use to label public space track what a neighbourhood is about, what languages and traditions it draws from, what it remembers and what it advertises. Namescape lets you ask questions like "where in Istanbul do shop names mention water?", "which Paris arrondissements have the most café names referencing the sea?", "which London boroughs name their pubs after kings vs. queens?" — and get a colored map back in a few seconds.

You give it: a city, a list of keywords, a set of place types to look at (shops, places of worship, government offices, …), and a matching mode. It gives back: a heatmap per district, a sortable table, and clickable district panels that list every matched place name.

## How matching works, briefly

Each place has a primary name plus optional alternative names (translations, historical names, brand names). The app searches across all of them. Four matching modes: **exact** (whole-word), **substring** (any part of the name), **root** (stem-aware — catches Turkish `kitap` ↔ `kitapçı` ↔ `kitabı`, French `boulanger` ↔ `boulangerie`, etc.), and **regex**. Matching is diacritic-insensitive. There's an exclude list for filtering out false positives (include `kara`, exclude `karaköy` if you don't want the Istanbul neighbourhood name).

You can color districts by **fraction** (matches as a share of filtered places in that district, displayed as ‰) or by **total matches**. A "min matches" threshold greys out low-volume districts so a one-shop hit in a quiet borough doesn't dominate the colormap.

## Where the data comes from

Places of interest (POIs) are merged from up to three open sources: **OpenStreetMap** (the most carefully curated, richest local naming), **Overture Maps Places** (a large, structured aggregation of multiple providers), and optionally **Foursquare OS Places** (the largest, but the noisiest). Cross-source duplicates are deduplicated spatially + by name. District polygons come from OSM administrative relations (or a city open-data portal where OSM is unreliable).

The current build of the live site uses OSM + Overture in Istanbul.

## Caveats

- Coverage is uneven. OSM mapping density varies wildly between cities and between districts within the same city. A district that looks "low" on a map may just be under-mapped, not actually low. The "total" column in the results table is your sanity check.
- Names are noisy. A bakery called "Karadeniz Fırını" matches `kara` even though the salient word is the regional reference. Use exclude lists, narrower keywords, or the `exact`/`regex` modes to tighten things up.
- Categories are heuristic. OSM has structured tags (`shop=bakery`); Overture and FSQ have free-form category strings that are remapped onto OSM tags via a hand-maintained mapping. Long-tail categories may end up under "Uncategorized".
- This is a static snapshot. The data is whatever the sources looked like at the last build — not live.

---

# Technical

## What the build does, step by step

For each city, `static/build.py` runs the following stages and writes a single file `static/site/data/<city>.json.gz`:

1. **Fetch district polygons.** Either via Overpass (an OSM administrative relation at a given `admin_level`) or via a direct GeoJSON URL.
2. **Fetch POIs from one or more sources** (configurable via `--sources`). Each source is normalized into a uniform record `{name, alt_names, lat, lon, category, address, ...}`. Lifecycle filters drop closed places (OSM `disused:`/`abandoned:`/etc., FSQ `date_closed`, Overture release-level pruning). Raw fetches are cached under `.fetch_cache/`; pass `--refetch` to invalidate.
3. **Map external categories to OSM tags.** Overture/FSQ category strings (`"Retail > Bakery"`) are rewritten onto OSM spec ids (`shop=bakery`) via `category_map.py` so sidebar checkboxes work uniformly across sources. Unmapped strings land under `external=other` and counts get dumped to `.fetch_cache/unmapped_categories__<city>.tsv` for review.
4. **Spatial + name dedup across sources** (`merge.py`). Records are bucket-gridded at ~100 m. Within 100 m, two records collapse into one when their name token sets are similar enough (subset, Jaccard ≥ 0.4, or overlap-coefficient ≥ 0.75). Priority order is configurable (default `osm > fsq > overture`); the higher-priority record keeps its display name and absorbs the others' names into its alt list.
5. **Point-in-polygon to attach a district.** Shapely STRtree spatial index over the district polygons. Records outside every polygon are dropped.
6. **Pre-stem name tokens.** Tokens are normalized (diacritic-stripped, Turkish-aware lowercase) and stemmed via Snowball in the city's language, then stored in the bundle so the **root** matching mode runs in the browser without shipping a stemmer.
7. **Write the bundle** as gzipped JSON (`<city>.json.gz`, ~25–30 MB for Istanbul). The frontend decompresses it on the fly via `DecompressionStream`. A separate `data/config.json` holds the tag tree, palettes, and city metadata index loaded at page startup.

## Bundle schema

```jsonc
{
  "meta":      { "id", "label", "country", "subdivision", "stemmer_language", "map_center", "map_zoom", "n_elements", "n_districts", "built_at" },
  "districts": <GeoJSON FeatureCollection of district polygons>,
  "elements":  [ { "n": "name", "d": "district", "c": "category label", "s": "spec_id", "lat": …, "lon": …, "stems": […], "src": "o|f|v",  /* osm/fsq/overture */ "ns": "alt | names",  /* optional */ "a":  "address",      /* optional */ "w":  "website",      /* osm only */ "oid": …, "otype": "n|w|r|x" }, … ]
}
```

## Sources at a glance

| source | mechanism | auth | Istanbul raw count |
|---|---|---|---:|
| **OpenStreetMap** | Overpass API | none | ~110k |
| **Overture Maps Places** | DuckDB on S3 parquet | none (anonymous) | ~430k |
| **Foursquare OS Places** | DuckDB on HF parquet | `HF_TOKEN` env var | ~1.4M |

## Install

Python 3.10+: `pip install -r requirements.txt`.

## Build

```bash
python -m static.build                              # build all cities
python -m static.build --only istanbul              # just one
python -m static.build --only istanbul,paris        # selected cities
python -m static.build --force                      # ignore existing bundles
python -m static.build --sources osm                # OSM only
python -m static.build --sources osm,overture       # OSM + Overture
python -m static.build --sources osm,overture,fsq   # all three (default)
python -m static.build --refetch                    # bypass .fetch_cache
python -m static.build --reindex                    # rewrite config.json only
```

| flag | what it does |
|---|---|
| `--only <ids>` | comma-separated city ids to build (default: all) |
| `--force` | rewrite a city bundle even if `<city>.json.gz` exists |
| `--sources <list>` | subset of `osm,overture,fsq` (default: all three). Sources not listed are not fetched and not merged |
| `--refetch` | invalidate `.fetch_cache/` and re-query the network |
| `--reindex` | only rebuild `data/config.json` from existing bundles (no source fetches) |
| `--sleep <s>` | sleep between cities (default 20 s) to avoid Overpass rate limits |

A city bundle is only rewritten when missing or when `--force` is passed. `data/config.json` is rewritten every run. First-run timing on an Istanbul-sized city: Overpass ~30 s, Overture ~30–90 s, Foursquare ~1–5 min, merge + PIP + write ~10–20 s. Subsequent runs hit the cache and finish in seconds.

## Enabling Foursquare

FSQ is gated on Hugging Face. To turn it on: visit https://huggingface.co/datasets/foursquare/fsq-os-places and click *Agree and access repository*; create a read token at https://huggingface.co/settings/tokens; then `export HF_TOKEN=hf_xxx` in the shell where you run the build. Without `HF_TOKEN`, FSQ is silently skipped.

## Add a new city

Edit `static/cities.py` and append an entry like:

```python
{
    "id": "lisbon",
    "label": "Lisbon",
    "country": "Portugal",
    "subdivision": "freguesia",
    "stemmer_language": "portuguese",
    "polygon_source": {"type": "osm", "relation_id": 5400890, "admin_level": 9, "name_field": "name"},
    "poi_area": {"type": "osm_relation", "relation_id": 5400890},
    "map_center": [38.722, -9.139],
    "map_zoom": 11,
},
```

Two settings worth thinking about: `admin_level` (which OSM admin level holds the districts you want — 6 for larger units, 8–9 for neighbourhood-scale; check the city in OSM to see what its districts use) and `stemmer_language` (Snowball language name used to pre-stem tokens for the **root** matching mode; if your language isn't supported, the bundle still works but stick to substring/regex).

For cities where OSM districts are unreliable, use a direct GeoJSON URL instead:

```python
"polygon_source": {"type": "url", "url": "https://example.org/districts.geojson", "name_field": "DistrictName"}
```

Then `python -m static.build --only lisbon` and redeploy `static/site/`.

## Run locally

```bash
python -m http.server 8000 --directory static/site
# open http://127.0.0.1:8000
```

## Project layout

```
static/
├── cities.py          city configs (polygon source, stemmer language, map view)
├── overpass.py        Overpass URL/UA + the is_open lifecycle filter
├── overture.py        Overture Maps fetcher (anonymous S3 parquet via DuckDB)
├── foursquare.py      Foursquare OS Places fetcher (HF_TOKEN required)
├── tags_config.py     the tag tree shown in the sidebar
├── category_map.py    Overture/FSQ category-string → OSM spec mapping
├── category_map.tsv   reviewed mapping rows (source of truth for category_map.py)
├── merge.py           cross-source spatial + name dedup
├── palettes.py        color stops per palette name
├── audit_dups.py      debugging: surface likely-missed duplicates after merge
├── build.py           orchestrator: districts → POIs → merge → bundle
└── site/              the deployable site
    ├── index.html  app.js  style.css
    └── data/                       (built by build.py)
        ├── config.json              tag tree + palettes + city index
        └── <city>.json.gz           districts + POIs, one per city
.fetch_cache/                        per-source raw fetch cache (gitignored)
requirements.txt
```

## Citation

```bibtex
@software{colas2026namescape,
  author = {Colas, C{\'e}dric and Ayd{\i}n, Hazal, Hazal},
  title  = {Namescape: Geospatial linguistic landscape analysis from open POI sources},
  year   = {2026},
  url    = {https://github.com/ccolas/namescape},
}
```

## Author & data attribution

Built by [Cédric Colas](https://cedriccolas.com). POI data from [OpenStreetMap](https://www.openstreetmap.org) (ODbL), [Overture Maps](https://overturemaps.org) (CDLA-Permissive-2.0 / ODbL depending on layer), and [Foursquare OS Places](https://opensource.foursquare.com/os-places/) (Apache-2.0). Polygon sources: OSM `admin_level` relations for most cities; the [Chicago Open Data portal](https://data.cityofchicago.org) for Chicago Community Areas.
