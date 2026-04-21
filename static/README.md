# Namescape — static build

Pure HTML/CSS/JS version of Namescape that runs entirely in the browser.
All Overpass queries happen at **build time**. The deployed site just
ships pre-computed per-city JSON bundles and does filter + matching +
aggregation in-memory.

## What's here

```
static/
├── cities.py          config: 6 cities + polygon source + stemmer lang
├── build.py           one-shot build: fetch polygons + POIs, PIP,
│                       pre-stem, write site/data/<city>.json
└── site/              the deployable site
    ├── index.html
    ├── app.js
    ├── style.css
    └── data/          built by build.py
        ├── config.json      tag tree + palettes + city index
        └── <city>.json      districts GeoJSON + POI records (one per city)
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

To add a city: append an entry to `static/cities.py`, then `python -m static.build --only <new_id>`.

## Build

First-time build fetches ~6 districts queries + ~6 big POI queries from
Overpass. Expect ~5–15 minutes total (Overpass response times vary).

```
# in your conda env with project deps installed
python -m static.build               # build all cities
python -m static.build --only paris  # just one
python -m static.build --force       # ignore existing data/<city>.json
```

A city bundle (`data/<city>.json`) is only rewritten when missing or when
`--force` is passed. `data/config.json` is rewritten every run.

## Run locally

```
cd static/site
python -m http.server 8000
# open http://127.0.0.1:8000
```

Leaflet + Inter + CartoDB tiles load from CDNs; everything else is local.

## Deploy to GitHub Pages

In your repo settings, point GitHub Pages at either:

- **The `/docs` folder on main** — rename `static/site` to `docs/` or
  symlink.
- **A `gh-pages` branch** — push the contents of `static/site/` to that
  branch.

Or push `static/site/` to any static host (Cloudflare Pages, Netlify,
S3+CloudFront, etc.). No build step required — it's already built.

## Matching modes

The static build ships three modes; "root" uses pre-computed Snowball
stems in the bundle, so the JS runtime doesn't need a stemmer.

- **substring** — diacritic-insensitive substring on the full name.
- **root** — loose root-word matching against pre-stemmed tokens. Catches
  suffix variants in inflected languages (kitap → kitapçı, kitaplar,
  kitabı). Implemented as bidirectional 4-char prefix + substring match
  on stems.
- **regex** — Python-style regex, case-insensitive, applied to the
  original name (with diacritics preserved).

This is slightly looser than the server version (which runs the Snowball
stemmer on the keyword too); differences are rare in practice.

## Refreshing data

OSM changes all the time. Re-run the build monthly or whenever you want
fresh data, then commit and redeploy. No runtime Overpass traffic is ever
caused by your visitors.

## Author

[Cédric Colas](https://cedriccolas.com)
