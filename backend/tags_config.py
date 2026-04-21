"""Tag tree exposed to the frontend.

Each tag is { label, key, value, filter_key?, filter_value? }.
value == "*" means "any value for this key".
filter_key/filter_value adds an extra OSM tag filter (e.g. religion=muslim).
"""

TAG_CATEGORIES = [
    {
        "id": "shops",
        "label": "Shops",
        "tags": [
            {"label": "All shops", "key": "shop", "value": "*"},
            {"label": "Supermarket", "key": "shop", "value": "supermarket"},
            {"label": "Convenience", "key": "shop", "value": "convenience"},
            {"label": "Bakery", "key": "shop", "value": "bakery"},
            {"label": "Butcher", "key": "shop", "value": "butcher"},
            {"label": "Greengrocer", "key": "shop", "value": "greengrocer"},
            {"label": "Clothes", "key": "shop", "value": "clothes"},
            {"label": "Shoes", "key": "shop", "value": "shoes"},
            {"label": "Jewelry", "key": "shop", "value": "jewelry"},
            {"label": "Hairdresser", "key": "shop", "value": "hairdresser"},
            {"label": "Books", "key": "shop", "value": "books"},
            {"label": "Florist", "key": "shop", "value": "florist"},
            {"label": "Optician", "key": "shop", "value": "optician"},
            {"label": "Kiosk", "key": "shop", "value": "kiosk"},
            {"label": "Mobile phone", "key": "shop", "value": "mobile_phone"},
            {"label": "Car repair", "key": "shop", "value": "car_repair"},
        ],
    },
    {
        "id": "religious",
        "label": "Religious institutions",
        "tags": [
            {"label": "All places of worship", "key": "amenity", "value": "place_of_worship"},
            {"label": "Muslim", "key": "amenity", "value": "place_of_worship",
             "filter_key": "religion", "filter_value": "muslim"},
            {"label": "Christian", "key": "amenity", "value": "place_of_worship",
             "filter_key": "religion", "filter_value": "christian"},
            {"label": "Jewish", "key": "amenity", "value": "place_of_worship",
             "filter_key": "religion", "filter_value": "jewish"},
        ],
    },
    {
        "id": "government",
        "label": "Government / official",
        "tags": [
            {"label": "Government office", "key": "office", "value": "government"},
            {"label": "Townhall", "key": "amenity", "value": "townhall"},
            {"label": "Courthouse", "key": "amenity", "value": "courthouse"},
            {"label": "Police", "key": "amenity", "value": "police"},
            {"label": "Post office", "key": "amenity", "value": "post_office"},
            {"label": "Fire station", "key": "amenity", "value": "fire_station"},
        ],
    },
    {
        "id": "education",
        "label": "Education",
        "tags": [
            {"label": "School", "key": "amenity", "value": "school"},
            {"label": "University", "key": "amenity", "value": "university"},
            {"label": "College", "key": "amenity", "value": "college"},
            {"label": "Kindergarten", "key": "amenity", "value": "kindergarten"},
            {"label": "Language school", "key": "amenity", "value": "language_school"},
            {"label": "Library", "key": "amenity", "value": "library"},
        ],
    },
    {
        "id": "healthcare",
        "label": "Healthcare",
        "tags": [
            {"label": "Hospital", "key": "amenity", "value": "hospital"},
            {"label": "Clinic", "key": "amenity", "value": "clinic"},
            {"label": "Pharmacy", "key": "amenity", "value": "pharmacy"},
            {"label": "Doctors", "key": "amenity", "value": "doctors"},
            {"label": "Dentist", "key": "amenity", "value": "dentist"},
            {"label": "Veterinary", "key": "amenity", "value": "veterinary"},
        ],
    },
    {
        "id": "food_drink",
        "label": "Food & drink",
        "tags": [
            {"label": "Restaurant", "key": "amenity", "value": "restaurant"},
            {"label": "Cafe", "key": "amenity", "value": "cafe"},
            {"label": "Bar", "key": "amenity", "value": "bar"},
            {"label": "Pub", "key": "amenity", "value": "pub"},
            {"label": "Fast food", "key": "amenity", "value": "fast_food"},
            {"label": "Nightclub", "key": "amenity", "value": "nightclub"},
        ],
    },
    {
        "id": "culture",
        "label": "Culture",
        "tags": [
            {"label": "Theatre", "key": "amenity", "value": "theatre"},
            {"label": "Cinema", "key": "amenity", "value": "cinema"},
            {"label": "Arts centre", "key": "amenity", "value": "arts_centre"},
            {"label": "Museum", "key": "tourism", "value": "museum"},
            {"label": "Gallery", "key": "tourism", "value": "gallery"},
        ],
    },
    {
        "id": "leisure",
        "label": "Leisure",
        "tags": [
            {"label": "Park", "key": "leisure", "value": "park"},
            {"label": "Sports centre", "key": "leisure", "value": "sports_centre"},
            {"label": "Fitness centre", "key": "leisure", "value": "fitness_centre"},
            {"label": "Playground", "key": "leisure", "value": "playground"},
        ],
    },
    {
        "id": "transport",
        "label": "Transport",
        "tags": [
            {"label": "Bus station", "key": "amenity", "value": "bus_station"},
            {"label": "Ferry terminal", "key": "amenity", "value": "ferry_terminal"},
            {"label": "Railway station", "key": "railway", "value": "station"},
            {"label": "Parking", "key": "amenity", "value": "parking"},
        ],
    },
]
