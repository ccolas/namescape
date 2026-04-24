"""Map Overture / Foursquare category labels to OSM-style spec IDs.

Mapping data lives in `category_map.tsv` (curated, easy to extend). At
import time we load the TSV into a case-insensitive dict. `map_external`
tries:

  1. Exact (case-insensitive) match on the FULL category string.
  2. Exact match on each `>`-separated segment, last-first (so a FSQ
     hierarchy like "Retail > Furniture and Home Store" hits
     "Furniture and Home Store" first, falls back to "Retail").
  3. Broad regex fallbacks (anything-`Store` → shop=*, anything-
     `Office`/`Service`/`Agency` → office=company, etc.) — every spec_id
     produced here must exist in tags_config.py or it'll be unfilterable.
  4. Final fallback: `external=other`.

To add or change mappings, edit `category_map.tsv` and rebuild — only
the merge step re-runs (cache hit on all source fetches).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

OTHER = "external=other"

_TSV_PATH = Path(__file__).resolve().parent / "category_map.tsv"


def _load_tsv() -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not _TSV_PATH.exists():
        return out
    for line in _TSV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        raw, sid = parts[0].strip(), parts[1].strip()
        if not raw or not sid:
            continue
        out[raw.lower()] = sid
    return out


_LOOKUP: Dict[str, str] = _load_tsv()


# Broad fallbacks for anything not in the TSV. Order matters; first
# match wins. These let us cover the long tail without enumerating
# thousands of leaf categories.
_FALLBACKS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"restaurant\b", re.I),     "amenity=restaurant"),
    (re.compile(r"\bcaf[eé]\b", re.I),      "amenity=cafe"),
    (re.compile(r"coffee", re.I),           "amenity=cafe"),
    (re.compile(r"\bbar\b", re.I),          "amenity=bar"),
    (re.compile(r"\bpub\b", re.I),          "amenity=pub"),
    (re.compile(r"\bjoint\b|fast[ _]?food", re.I), "amenity=fast_food"),
    (re.compile(r"night[ _]?club|nightlife", re.I), "amenity=nightclub"),

    (re.compile(r"\bmosque\b", re.I),       "amenity=place_of_worship|religion=muslim"),
    (re.compile(r"\bchurch\b|chapel|cathedral", re.I), "amenity=place_of_worship|religion=christian"),
    (re.compile(r"\bsynagogue\b", re.I),    "amenity=place_of_worship|religion=jewish"),
    (re.compile(r"place[ _]?of[ _]?worship|religious", re.I), "amenity=place_of_worship"),

    (re.compile(r"jewel|kuyumcu", re.I),    "shop=jewelry"),
    (re.compile(r"bakery", re.I),           "shop=bakery"),
    (re.compile(r"butcher", re.I),          "shop=butcher"),
    (re.compile(r"florist|flower\b", re.I), "shop=florist"),
    (re.compile(r"hardware", re.I),         "shop=doityourself"),
    (re.compile(r"hairdresser|barber|hair[ _]?salon", re.I), "shop=hairdresser"),
    (re.compile(r"\bbook\b|bookstore", re.I), "shop=books"),
    (re.compile(r"\bshoe\b|footwear", re.I), "shop=shoes"),
    (re.compile(r"clothing|\bclothes\b|fashion|apparel|boutique", re.I), "shop=clothes"),
    (re.compile(r"furniture", re.I),        "shop=furniture"),
    (re.compile(r"electronic", re.I),       "shop=electronics"),
    (re.compile(r"department[ _]?store", re.I), "shop=department_store"),
    (re.compile(r"\bmall\b|shopping[ _]?(center|centre|mall)", re.I), "shop=mall"),
    (re.compile(r"convenience", re.I),      "shop=convenience"),
    (re.compile(r"supermarket|grocery", re.I), "shop=supermarket"),
    (re.compile(r"optician|optical|eyewear|eyecare", re.I), "shop=optician"),
    (re.compile(r"mobile[ _]?phone|cell[ _]?phone|phone[ _]?store", re.I), "shop=mobile_phone"),
    (re.compile(r"car[ _]?(repair|service|wash|garage)|auto[ _]?(repair|shop)", re.I), "shop=car_repair"),
    (re.compile(r"car[ _]?dealer|used[ _]?car", re.I), "shop=car"),
    (re.compile(r"beauty|nail|spa\b|cosmetics", re.I), "shop=beauty"),
    (re.compile(r"dessert|candy|sweet|patisserie|pastry", re.I), "shop=confectionery"),
    (re.compile(r"\btoy\b", re.I),          "shop=toys"),
    (re.compile(r"\bpet\b", re.I),          "shop=pet"),

    (re.compile(r"\bschool\b", re.I),       "amenity=school"),
    (re.compile(r"university", re.I),       "amenity=university"),
    (re.compile(r"\bcollege\b", re.I),      "amenity=college"),
    (re.compile(r"kindergarten|preschool|nursery|day[ _]?care", re.I), "amenity=kindergarten"),
    (re.compile(r"\blibrary\b", re.I),      "amenity=library"),

    (re.compile(r"hospital", re.I),         "amenity=hospital"),
    (re.compile(r"clinic|polyclinic|medical[ _]?center", re.I), "amenity=clinic"),
    (re.compile(r"pharmacy|drug[ _]?store", re.I), "amenity=pharmacy"),
    (re.compile(r"dentist|dental", re.I),   "amenity=dentist"),
    (re.compile(r"\bdoctor\b|physician", re.I), "amenity=doctors"),
    (re.compile(r"veterinar", re.I),        "amenity=veterinary"),

    (re.compile(r"\bbank\b", re.I),         "amenity=bank"),
    (re.compile(r"\batm\b", re.I),          "amenity=atm"),
    (re.compile(r"currency[ _]?exchange|bureau[ _]?de[ _]?change", re.I), "amenity=bureau_de_change"),
    (re.compile(r"insurance", re.I),        "office=insurance"),
    (re.compile(r"\blawyer\b|attorney|legal[ _]?service", re.I), "office=lawyer"),
    (re.compile(r"real[ _]?estate", re.I),  "office=estate_agent"),
    (re.compile(r"travel[ _]?agency|travel[ _]?agent", re.I), "office=travel_agent"),
    (re.compile(r"advertising", re.I),      "office=advertising_agency"),
    (re.compile(r"coworking", re.I),        "office=coworking"),
    (re.compile(r"\bnon[- _]?profit|\bngo\b|charit", re.I), "office=ngo"),
    (re.compile(r"government[ _]?(office|building)", re.I), "office=government"),
    (re.compile(r"town[ _]?hall|city[ _]?hall", re.I), "amenity=townhall"),
    (re.compile(r"courthouse|\bcourt\b", re.I), "amenity=courthouse"),
    (re.compile(r"police", re.I),           "amenity=police"),
    (re.compile(r"post[ _]?office", re.I),  "amenity=post_office"),
    (re.compile(r"fire[ _]?station", re.I), "amenity=fire_station"),
    (re.compile(r"\boffice\b|business[ _]?(center|centre|service)|professional[ _]?service|consult", re.I),
        "office=company"),
    (re.compile(r"\bagency\b|\bservices?\b", re.I), "office=company"),

    (re.compile(r"hotel|motel|hostel|guest[ _]?house|bed[ _]?and[ _]?breakfast|resort|inn\b|accommodation", re.I),
        "tourism=hotel"),
    (re.compile(r"museum", re.I),           "tourism=museum"),
    (re.compile(r"gallery|art[ _]?gallery", re.I), "tourism=gallery"),
    (re.compile(r"theme[ _]?park", re.I),   "tourism=theme_park"),
    (re.compile(r"aquarium", re.I),         "tourism=aquarium"),
    (re.compile(r"\bzoo\b", re.I),          "tourism=zoo"),
    (re.compile(r"viewpoint|attraction|landmark|monument|historic|protected[ _]?site|memorial", re.I),
        "historic=monument"),
    (re.compile(r"tourist[ _]?information", re.I), "tourism=information"),

    (re.compile(r"\bpark\b", re.I),         "leisure=park"),
    (re.compile(r"\bgarden\b", re.I),       "leisure=garden"),
    (re.compile(r"playground", re.I),       "leisure=playground"),
    (re.compile(r"sports?[ _]?(centre|center)", re.I), "leisure=sports_centre"),
    (re.compile(r"fitness|\bgym\b|yoga|pilates|cross[ _]?fit", re.I), "leisure=fitness_centre"),
    (re.compile(r"stadium|arena", re.I),    "leisure=stadium"),
    (re.compile(r"swim|pool", re.I),        "leisure=swimming_pool"),
    (re.compile(r"bowling", re.I),          "leisure=bowling_alley"),
    (re.compile(r"arcade", re.I),           "leisure=amusement_arcade"),
    (re.compile(r"golf", re.I),             "leisure=golf_course"),
    (re.compile(r"music[ _]?venue|concert[ _]?hall|performing[ _]?arts", re.I), "amenity=arts_centre"),
    (re.compile(r"theat(re|er)", re.I),     "amenity=theatre"),
    (re.compile(r"cinema|movie[ _]?theat(re|er)", re.I), "amenity=cinema"),
    (re.compile(r"arts[ _]?(centre|center)", re.I), "amenity=arts_centre"),

    (re.compile(r"bus[ _]?(station|terminal)", re.I), "amenity=bus_station"),
    (re.compile(r"ferry[ _]?(terminal|dock)", re.I), "amenity=ferry_terminal"),
    (re.compile(r"(railway|train)[ _]?station|metro[ _]?station|subway[ _]?station", re.I), "railway=station"),
    (re.compile(r"\bparking\b|car[ _]?park", re.I), "amenity=parking"),
    (re.compile(r"gas[ _]?station|fuel|petrol", re.I), "amenity=fuel"),
    (re.compile(r"car[ _]?rental", re.I),   "amenity=car_rental"),

    (re.compile(r"\bcraft\b|workshop\b", re.I), "craft=*"),
    (re.compile(r"laundromat|laundry|dry[ _]?clean", re.I), "shop=laundry"),

    # ---- food variants the leaf labels miss ----
    (re.compile(r"pizzeria|bistro|deli\b|cafeteria|brewpub|gastropub|eatery|tavern", re.I), "amenity=restaurant"),
    (re.compile(r"\blounge\b", re.I),       "amenity=bar"),
    (re.compile(r"\bbrewery\b|wine\s?bar|beer\s?garden", re.I), "amenity=bar"),
    (re.compile(r"snack|food\s?truck|kebab|kofte|cigkofte|doner|shawarma", re.I), "amenity=fast_food"),
    (re.compile(r"\bmarket\b|marketplace|bazaar|flea\s?market", re.I), "amenity=marketplace"),

    # ---- venues / community ----
    (re.compile(r"event\s?(space|venue)|wedding\s?hall|conference\s?room|meeting\s?room|auditorium|banquet", re.I),
        "amenity=events_venue"),
    (re.compile(r"community\s?(centre|center)|cultural\s?(centre|center)|recreation\s?(centre|center)|club\s?house|social\s?club", re.I),
        "amenity=community_centre"),
    (re.compile(r"comedy\s?club", re.I),    "amenity=theatre"),

    # ---- outdoors / sights ----
    (re.compile(r"viewpoint|scenic\s?lookout|overlook|panorama", re.I), "tourism=viewpoint"),
    (re.compile(r"public\s?art|sculpture|street\s?art|mural", re.I), "tourism=artwork"),
    (re.compile(r"campground|camp\s?site|caravan", re.I), "tourism=camp_site"),
    (re.compile(r"harbor|harbour|marina", re.I), "leisure=marina"),

    # ---- transport / services ----
    (re.compile(r"\btaxi\b|taxi\s?stand|cab\s?service", re.I), "amenity=taxi"),
    (re.compile(r"transportation|travel\s?service|freight|cargo|logistics|shipping", re.I), "office=travel_agent"),
    (re.compile(r"emergency\s?room|er\b", re.I), "amenity=hospital"),
    (re.compile(r"corporate\s?(office|amenity)|software\s?dev|tech\b|it\s?service", re.I), "office=it"),
    (re.compile(r"architectural|architect", re.I), "office=architect"),
    (re.compile(r"photography\s?lab|photo\s?studio|photo\s?lab", re.I), "shop=photo"),
    (re.compile(r"financial", re.I),        "office=financial"),

    # ---- broaden bank/atm to handle plurals + snake_case ----
    (re.compile(r"\bbanks?\b|credit\s?union", re.I), "amenity=bank"),
    (re.compile(r"\batms?\b", re.I),        "amenity=atm"),

    # ---- pickups for bigger leftover buckets ----
    (re.compile(r"arts\s?and\s?entertainment|entertainment", re.I), "amenity=arts_centre"),
    (re.compile(r"automotive|auto\s?detailing", re.I), "shop=car_repair"),
    (re.compile(r"auto\s?parts|automotive\s?parts", re.I), "shop=car_parts"),
    (re.compile(r"sports\s?and\s?recreation|sports\s?club|athletic", re.I), "leisure=sports_centre"),
    (re.compile(r"strip\s?club|adult\s?club", re.I), "amenity=stripclub"),
    (re.compile(r"dance\s?(club|hall)", re.I), "amenity=nightclub"),
    (re.compile(r"dance\s?studio|ballet", re.I), "leisure=dance"),
    (re.compile(r"castle|fortress|fort\b", re.I), "historic=castle"),
    (re.compile(r"capitol|parliament|legislative", re.I), "office=government"),
    (re.compile(r"public\s?and\s?government|government\s?association", re.I), "office=government"),
    (re.compile(r"borek|soup|pide|meyhane|kebabci|lokanta|esnaf", re.I), "amenity=restaurant"),
    (re.compile(r"television|tv\s?station|radio\s?station", re.I), "office=company"),
    (re.compile(r"telecom|telephone\s?company", re.I), "office=telecommunication"),
    (re.compile(r"psychologist|psychiatr|therapist|therapy|counsel", re.I), "amenity=clinic"),
    (re.compile(r"medical\s?lab|laboratory", re.I), "amenity=clinic"),
    (re.compile(r"contractor|construction\s?company", re.I), "craft=*"),
    (re.compile(r"storage\s?facility|self\s?storage", re.I), "shop=storage_rental"),
    (re.compile(r"\bshopping\b", re.I),     "shop=*"),  # bare overture leaf
    (re.compile(r"miscellaneous", re.I),    "shop=*"),
    (re.compile(r"rest\s?area|picnic", re.I), "external=other"),
    (re.compile(r"hiking|trail\b", re.I),   "external=other"),
    (re.compile(r"\btree\b|\btown\b|\bisland\b|\bfair\b|\bvillage\b", re.I), "external=other"),

    # Last-line catch-alls — bucket leftovers into the existing OSM-wide
    # "Other …" checkboxes so the user can still toggle them.
    (re.compile(r"\bstore\b|\bshop\b|retail", re.I), "shop=*"),
    (re.compile(r"factory|manufacturing|industrial|warehouse|distribution|b2b\b|equipment|cargo", re.I), "external=other"),
    (re.compile(r"residential|apartment|condo|housing|dwelling|home\s?developer|campus\s?building", re.I), "external=other"),
    (re.compile(r"\broad\b|highway|\bstreet\b|bridge|tunnel|intersection|bus\s?(stop|line)|plane|boat\s?or\s?ferry", re.I), "external=other"),
    (re.compile(r"cemetery|grave\s?yard|graveyard", re.I), "amenity=grave_yard"),
    (re.compile(r"\bstructure\b|neighborhood|\bplaza\b|\bfield\b|\bforest\b|military\s?base|moving\s?target|public\s?safety|construction\s?services?", re.I),
        "external=other"),
]


def map_external(raw_category: str) -> str:
    if not raw_category:
        return OTHER
    cat = raw_category.replace(">", "/").strip()
    segments = [s.strip() for s in cat.split("/") if s.strip()]
    candidates = [cat] + list(reversed(segments))

    # 1 + 2: TSV exact (case-insensitive) lookup on full string then segments.
    # Normalize underscores → spaces so Overture's snake_case (`tea_room`)
    # matches a TSV entry written as "Tea Room".
    for c in candidates:
        key = c.lower().replace("_", " ")
        sid = _LOOKUP.get(key)
        if sid:
            return sid

    # 3: regex fallbacks. Normalize underscores → spaces so Overture-style
    # snake_case (`bank_credit_union`) matches the same patterns as the
    # FSQ "Bank Credit Union" form.
    for c in candidates:
        norm = c.replace("_", " ")
        for rx, sid in _FALLBACKS:
            if rx.search(norm):
                return sid

    # 4: final.
    return OTHER
