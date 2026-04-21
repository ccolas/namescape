"""6-city configuration for the static Namescape site.

Each entry carries:
  - id / label / country
  - stemmer_language: Snowball language name; used by build.py when
    pre-stemming name tokens so the frontend can do loose root matching
  - polygon_source: how to obtain district polygons. Two shapes:
      * {"type": "osm", "relation_id": <int>, "admin_level": <int>,
         "name_field": "name"}
        — fetch OSM relation; inside its area, grab every relation at the
        given admin_level, convert to a GeoJSON FeatureCollection
      * {"type": "url", "url": <url>, "name_field": <str>}
        — fetch GeoJSON directly from an open-data portal
  - poi_area: where to query POIs from. Either {"type": "osm_relation",
    "relation_id": ...} or {"type": "bbox", ...} computed from polygons
    (use "derive_from_polygons")
  - map_center / map_zoom: Leaflet initial view
"""

CITIES = [
    {
        "id": "istanbul",
        "label": "Istanbul",
        "country": "Turkey",
        "subdivision": "ilçe",
        "stemmer_language": "turkish",
        "polygon_source": {
            "type": "osm",
            "relation_id": 223474,
            "admin_level": 6,
            "name_field": "name",
        },
        "poi_area": {"type": "osm_relation", "relation_id": 223474},
        "map_center": [41.05, 29.0],
        "map_zoom": 10,
    },
    {
        "id": "paris",
        "label": "Paris",
        "country": "France",
        "subdivision": "arrondissement",
        "stemmer_language": "french",
        "polygon_source": {
            "type": "osm",
            "relation_id": 7444,
            "admin_level": 9,
            "name_field": "name",
        },
        "poi_area": {"type": "osm_relation", "relation_id": 7444},
        "map_center": [48.857, 2.352],
        "map_zoom": 11,
    },
    {
        "id": "berlin",
        "label": "Berlin",
        "country": "Germany",
        "subdivision": "Bezirk",
        "stemmer_language": "german",
        "polygon_source": {
            "type": "osm",
            "relation_id": 62422,
            "admin_level": 9,
            "name_field": "name",
        },
        "poi_area": {"type": "osm_relation", "relation_id": 62422},
        "map_center": [52.52, 13.405],
        "map_zoom": 10,
    },
    {
        "id": "london",
        "label": "London",
        "country": "United Kingdom",
        "subdivision": "borough",
        "stemmer_language": "english",
        "polygon_source": {
            "type": "osm",
            "relation_id": 175342,
            "admin_level": 8,
            "name_field": "name",
        },
        "poi_area": {"type": "osm_relation", "relation_id": 175342},
        "map_center": [51.507, -0.128],
        "map_zoom": 10,
    },
    {
        "id": "madrid",
        "label": "Madrid",
        "country": "Spain",
        "subdivision": "distrito",
        "stemmer_language": "spanish",
        "polygon_source": {
            "type": "osm",
            "relation_id": 5326784,
            "admin_level": 9,
            "name_field": "name",
        },
        "poi_area": {"type": "osm_relation", "relation_id": 5326784},
        "map_center": [40.416, -3.703],
        "map_zoom": 11,
    },
    # Chicago was planned (77 Community Areas via Chicago Open Data portal)
    # but the Socrata geospatial-export endpoint returns empty payloads on the
    # first few hits (it queues a job) — not worth the flakiness. Re-add with
    # a different source if desired.
]

CITY_BY_ID = {c["id"]: c for c in CITIES}
