"""Curated destination catalogue used for candidate generation.

WHY A STATIC CATALOGUE
----------------------
"Which places should I even consider?" is a recall problem, and there is no
free, keyless API that answers it. Two mechanisms cover it here:

1. **The LLM proposes** destination names from the user's phrasing, and the geo
   server validates/enriches each one through OSM Nominatim. This handles the
   long tail ("somewhere like Tbilisi but cheaper").
2. **This catalogue provides a deterministic floor.** It guarantees the system
   returns sensible candidates with *no* API key at all, gives the LLM a
   grounded shortlist to re-rank instead of hallucinating coordinates, and
   makes the recommendation path reproducible in tests.

Each entry carries coordinates (so the weather server can be called without a
geocoding round-trip), interest tags, a coarse budget tier and the months the
destination is genuinely at its best. Coordinates are city-centre points.

``budget_tier``: 1 = very affordable, 2 = moderate, 3 = pricey, 4 = expensive.
``best_months``: months where climate and crowding are jointly favourable.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Canonical interest vocabulary. The Query Analyst maps free text onto these.
INTEREST_TAGS = (
    "beach", "culture", "history", "museums", "food", "nightlife", "hiking",
    "mountains", "nature", "wildlife", "diving", "surfing", "skiing",
    "architecture", "art", "shopping", "romantic", "family", "adventure",
    "wellness", "islands", "roadtrip", "photography", "budget", "luxury",
    "nomad", "festival", "desert", "lakes", "cycling", "wine", "nightlife",
)

DESTINATIONS: tuple[dict[str, Any], ...] = (
    # ---------------- Southern Europe ----------------
    {"city": "Lisbon", "country": "Portugal", "iso2": "PT", "region": "Southern Europe",
     "lat": 38.7223, "lon": -9.1393, "budget_tier": 2, "best_months": [4, 5, 6, 9, 10],
     "tags": ["culture", "food", "architecture", "nightlife", "beach", "nomad", "budget"],
     "blurb": "Tiled hillside city with Atlantic light, tram lines and a fierce food scene."},
    {"city": "Porto", "country": "Portugal", "iso2": "PT", "region": "Southern Europe",
     "lat": 41.1579, "lon": -8.6291, "budget_tier": 1, "best_months": [5, 6, 9, 10],
     "tags": ["food", "wine", "architecture", "culture", "budget", "romantic"],
     "blurb": "Port cellars, granite riverfront and the best value in Western Europe."},
    {"city": "Seville", "country": "Spain", "iso2": "ES", "region": "Southern Europe",
     "lat": 37.3891, "lon": -5.9845, "budget_tier": 2, "best_months": [3, 4, 5, 10, 11],
     "tags": ["history", "architecture", "food", "culture", "festival", "romantic"],
     "blurb": "Moorish palaces, orange trees and flamenco; brutal in high summer."},
    {"city": "Barcelona", "country": "Spain", "iso2": "ES", "region": "Southern Europe",
     "lat": 41.3874, "lon": 2.1686, "budget_tier": 3, "best_months": [5, 6, 9, 10],
     "tags": ["beach", "architecture", "art", "food", "nightlife", "culture"],
     "blurb": "Gaudi surrealism against a working Mediterranean beach city."},
    {"city": "Palma", "country": "Spain", "iso2": "ES", "region": "Southern Europe",
     "lat": 39.5696, "lon": 2.6502, "budget_tier": 3, "best_months": [5, 6, 9, 10],
     "tags": ["beach", "islands", "cycling", "food", "family", "luxury"],
     "blurb": "Mallorca's cathedral city, with cycling passes and quiet coves inland."},
    {"city": "Rome", "country": "Italy", "iso2": "IT", "region": "Southern Europe",
     "lat": 41.9028, "lon": 12.4964, "budget_tier": 3, "best_months": [4, 5, 9, 10],
     "tags": ["history", "museums", "food", "architecture", "culture", "art"],
     "blurb": "Three thousand years of layered ruins, and the food to survive them."},
    {"city": "Florence", "country": "Italy", "iso2": "IT", "region": "Southern Europe",
     "lat": 43.7696, "lon": 11.2558, "budget_tier": 3, "best_months": [4, 5, 9, 10],
     "tags": ["art", "museums", "architecture", "wine", "romantic", "culture"],
     "blurb": "Renaissance density per square metre unmatched anywhere."},
    {"city": "Naples", "country": "Italy", "iso2": "IT", "region": "Southern Europe",
     "lat": 40.8518, "lon": 14.2681, "budget_tier": 2, "best_months": [4, 5, 6, 9, 10],
     "tags": ["food", "history", "culture", "budget", "photography"],
     "blurb": "Chaotic, magnificent, and the birthplace of pizza; Pompeii is next door."},
    {"city": "Athens", "country": "Greece", "iso2": "GR", "region": "Southern Europe",
     "lat": 37.9838, "lon": 23.7275, "budget_tier": 2, "best_months": [4, 5, 9, 10],
     "tags": ["history", "museums", "food", "culture", "budget", "architecture"],
     "blurb": "The Acropolis above a gritty, rapidly improving modern capital."},
    {"city": "Santorini", "country": "Greece", "iso2": "GR", "region": "Southern Europe",
     "lat": 36.3932, "lon": 25.4615, "budget_tier": 4, "best_months": [5, 6, 9, 10],
     "tags": ["romantic", "islands", "beach", "luxury", "photography", "wine"],
     "blurb": "Caldera sunsets that earn the cliche, at a price that also earns it."},
    {"city": "Crete", "country": "Greece", "iso2": "GR", "region": "Southern Europe",
     "lat": 35.3387, "lon": 25.1442, "budget_tier": 2, "best_months": [5, 6, 9, 10],
     "tags": ["beach", "hiking", "history", "food", "family", "islands"],
     "blurb": "Big enough to hold gorges, Minoan ruins and empty south-coast beaches."},
    {"city": "Split", "country": "Croatia", "iso2": "HR", "region": "Southern Europe",
     "lat": 43.5081, "lon": 16.4402, "budget_tier": 2, "best_months": [5, 6, 9, 10],
     "tags": ["beach", "history", "islands", "nightlife", "adventure"],
     "blurb": "A Roman emperor's palace repurposed as a living old town, islands offshore."},
    {"city": "Dubrovnik", "country": "Croatia", "iso2": "HR", "region": "Southern Europe",
     "lat": 42.6507, "lon": 18.0944, "budget_tier": 3, "best_months": [5, 6, 9, 10],
     "tags": ["history", "architecture", "beach", "romantic", "photography"],
     "blurb": "Walled limestone city on the Adriatic; go in shoulder season or not at all."},
    {"city": "Valletta", "country": "Malta", "iso2": "MT", "region": "Southern Europe",
     "lat": 35.8989, "lon": 14.5146, "budget_tier": 2, "best_months": [4, 5, 6, 10],
     "tags": ["history", "diving", "architecture", "beach", "culture"],
     "blurb": "A fortress capital the size of a neighbourhood, with wreck diving offshore."},
    {"city": "Ljubljana", "country": "Slovenia", "iso2": "SI", "region": "Central Europe",
     "lat": 46.0569, "lon": 14.5058, "budget_tier": 2, "best_months": [5, 6, 9, 10],
     "tags": ["nature", "hiking", "lakes", "architecture", "cycling", "budget"],
     "blurb": "Pocket-sized green capital, an hour from Bled and the Julian Alps."},

    # ---------------- Western / Northern Europe ----------------
    {"city": "Paris", "country": "France", "iso2": "FR", "region": "Western Europe",
     "lat": 48.8566, "lon": 2.3522, "budget_tier": 4, "best_months": [4, 5, 6, 9, 10],
     "tags": ["art", "museums", "food", "romantic", "architecture", "shopping"],
     "blurb": "Still the benchmark for museums, pastry and walkable grandeur."},
    {"city": "Nice", "country": "France", "iso2": "FR", "region": "Western Europe",
     "lat": 43.7102, "lon": 7.2620, "budget_tier": 3, "best_months": [5, 6, 9, 10],
     "tags": ["beach", "food", "art", "romantic", "luxury", "roadtrip"],
     "blurb": "Riviera base camp: pebble beaches, Matisse, and hill villages behind."},
    {"city": "Amsterdam", "country": "Netherlands", "iso2": "NL", "region": "Western Europe",
     "lat": 52.3676, "lon": 4.9041, "budget_tier": 4, "best_months": [4, 5, 6, 9],
     "tags": ["museums", "art", "cycling", "nightlife", "architecture", "culture"],
     "blurb": "Canal grid, world-class galleries, and a city built for bicycles."},
    {"city": "Vienna", "country": "Austria", "iso2": "AT", "region": "Central Europe",
     "lat": 48.2082, "lon": 16.3738, "budget_tier": 3, "best_months": [4, 5, 6, 9, 12],
     "tags": ["museums", "art", "architecture", "culture", "wellness", "festival"],
     "blurb": "Imperial scale, concert halls, coffee houses, and December markets."},
    {"city": "Prague", "country": "Czechia", "iso2": "CZ", "region": "Central Europe",
     "lat": 50.0755, "lon": 14.4378, "budget_tier": 2, "best_months": [4, 5, 6, 9, 12],
     "tags": ["architecture", "history", "nightlife", "budget", "culture", "photography"],
     "blurb": "Gothic-to-Baroque skyline that survived the wars intact."},
    {"city": "Budapest", "country": "Hungary", "iso2": "HU", "region": "Eastern Europe",
     "lat": 47.4979, "lon": 19.0402, "budget_tier": 1, "best_months": [4, 5, 6, 9, 10],
     "tags": ["wellness", "architecture", "nightlife", "budget", "history", "food"],
     "blurb": "Thermal baths, ruin bars and Danube views at Eastern European prices."},
    {"city": "Krakow", "country": "Poland", "iso2": "PL", "region": "Eastern Europe",
     "lat": 50.0647, "lon": 19.9450, "budget_tier": 1, "best_months": [5, 6, 9, 10, 12],
     "tags": ["history", "culture", "budget", "food", "architecture"],
     "blurb": "Intact medieval core, sobering day trips, and remarkable value."},
    {"city": "Copenhagen", "country": "Denmark", "iso2": "DK", "region": "Northern Europe",
     "lat": 55.6761, "lon": 12.5683, "budget_tier": 4, "best_months": [5, 6, 7, 8],
     "tags": ["food", "architecture", "cycling", "culture", "luxury", "wellness"],
     "blurb": "Design, New Nordic cooking and harbour swimming - if you can afford it."},
    {"city": "Stockholm", "country": "Sweden", "iso2": "SE", "region": "Northern Europe",
     "lat": 59.3293, "lon": 18.0686, "budget_tier": 4, "best_months": [6, 7, 8],
     "tags": ["islands", "museums", "architecture", "nature", "culture", "family"],
     "blurb": "Fourteen islands, long summer light and unusually good museums."},
    {"city": "Reykjavik", "country": "Iceland", "iso2": "IS", "region": "Northern Europe",
     "lat": 64.1466, "lon": -21.9426, "budget_tier": 4, "best_months": [6, 7, 8, 9],
     "tags": ["nature", "adventure", "hiking", "photography", "roadtrip", "wildlife"],
     "blurb": "Base for waterfalls, glaciers and the Ring Road; aurora from September."},
    {"city": "Tromso", "country": "Norway", "iso2": "NO", "region": "Northern Europe",
     "lat": 69.6492, "lon": 18.9553, "budget_tier": 4, "best_months": [1, 2, 3, 6, 7, 9],
     "tags": ["nature", "wildlife", "adventure", "photography", "skiing"],
     "blurb": "Arctic city for northern lights in winter, midnight sun in summer."},
    {"city": "Edinburgh", "country": "United Kingdom", "iso2": "GB", "region": "Northern Europe",
     "lat": 55.9533, "lon": -3.1883, "budget_tier": 3, "best_months": [5, 6, 8, 9],
     "tags": ["history", "festival", "architecture", "hiking", "culture"],
     "blurb": "Volcanic crag, Old Town closes, and the world's biggest arts festival."},
    {"city": "Dublin", "country": "Ireland", "iso2": "IE", "region": "Northern Europe",
     "lat": 53.3498, "lon": -6.2603, "budget_tier": 3, "best_months": [5, 6, 7, 8, 9],
     "tags": ["culture", "nightlife", "history", "food", "roadtrip"],
     "blurb": "Literary pubs and a springboard for the Wild Atlantic Way."},
    {"city": "Zurich", "country": "Switzerland", "iso2": "CH", "region": "Western Europe",
     "lat": 47.3769, "lon": 8.5417, "budget_tier": 4, "best_months": [5, 6, 7, 8, 9],
     "tags": ["lakes", "mountains", "hiking", "luxury", "museums", "skiing"],
     "blurb": "Lakeside launchpad into the Alps; the most expensive city here."},
    {"city": "Innsbruck", "country": "Austria", "iso2": "AT", "region": "Central Europe",
     "lat": 47.2692, "lon": 11.4041, "budget_tier": 3, "best_months": [1, 2, 3, 6, 7, 8, 9],
     "tags": ["skiing", "mountains", "hiking", "adventure", "family", "nature"],
     "blurb": "A baroque old town with cable cars leaving from the city centre."},

    # ---------------- Turkiye / Caucasus / Middle East ----------------
    {"city": "Istanbul", "country": "Turkiye", "iso2": "TR", "region": "Western Asia",
     "lat": 41.0082, "lon": 28.9784, "budget_tier": 1, "best_months": [4, 5, 6, 9, 10],
     "tags": ["history", "food", "architecture", "shopping", "culture", "budget"],
     "blurb": "Two continents, Byzantine and Ottoman layers, and extraordinary food value."},
    {"city": "Cappadocia", "country": "Turkiye", "iso2": "TR", "region": "Western Asia",
     "lat": 38.6431, "lon": 34.8286, "budget_tier": 1, "best_months": [4, 5, 6, 9, 10],
     "tags": ["photography", "adventure", "history", "romantic", "desert", "budget"],
     "blurb": "Balloons over volcanic tuff valleys and cave-cut churches."},
    {"city": "Tbilisi", "country": "Georgia", "iso2": "GE", "region": "Western Asia",
     "lat": 41.7151, "lon": 44.8271, "budget_tier": 1, "best_months": [5, 6, 9, 10],
     "tags": ["food", "wine", "culture", "budget", "nomad", "mountains", "hiking"],
     "blurb": "Sulphur baths, an 8,000-year wine tradition, and Caucasus trailheads."},
    {"city": "Dubai", "country": "United Arab Emirates", "iso2": "AE", "region": "Western Asia",
     "lat": 25.2048, "lon": 55.2708, "budget_tier": 4, "best_months": [11, 12, 1, 2, 3],
     "tags": ["luxury", "shopping", "beach", "desert", "family", "architecture"],
     "blurb": "Engineered spectacle; only tolerable between November and March."},
    {"city": "Amman", "country": "Jordan", "iso2": "JO", "region": "Western Asia",
     "lat": 31.9539, "lon": 35.9106, "budget_tier": 2, "best_months": [3, 4, 5, 10, 11],
     "tags": ["history", "desert", "adventure", "culture", "photography"],
     "blurb": "Base for Petra, Wadi Rum and the Dead Sea within a few hours' drive."},

    # ---------------- Asia ----------------
    {"city": "Tokyo", "country": "Japan", "iso2": "JP", "region": "Eastern Asia",
     "lat": 35.6762, "lon": 139.6503, "budget_tier": 3, "best_months": [3, 4, 5, 10, 11],
     "tags": ["food", "culture", "shopping", "architecture", "nightlife", "art"],
     "blurb": "The densest, most functional megacity on earth; cherry blossom in April."},
    {"city": "Kyoto", "country": "Japan", "iso2": "JP", "region": "Eastern Asia",
     "lat": 35.0116, "lon": 135.7681, "budget_tier": 3, "best_months": [3, 4, 5, 10, 11],
     "tags": ["culture", "history", "architecture", "wellness", "romantic", "photography"],
     "blurb": "Sixteen hundred temples, machiya lanes, and autumn colour worth the crowds."},
    {"city": "Seoul", "country": "South Korea", "iso2": "KR", "region": "Eastern Asia",
     "lat": 37.5665, "lon": 126.9780, "budget_tier": 2, "best_months": [4, 5, 9, 10],
     "tags": ["food", "nightlife", "shopping", "culture", "hiking", "art"],
     "blurb": "Palaces, mountain trails inside the city, and 24-hour everything."},
    {"city": "Bangkok", "country": "Thailand", "iso2": "TH", "region": "South-eastern Asia",
     "lat": 13.7563, "lon": 100.5018, "budget_tier": 1, "best_months": [11, 12, 1, 2],
     "tags": ["food", "nightlife", "culture", "budget", "shopping", "nomad"],
     "blurb": "Street food capital and the cheapest big-city luxury anywhere."},
    {"city": "Chiang Mai", "country": "Thailand", "iso2": "TH", "region": "South-eastern Asia",
     "lat": 18.7883, "lon": 98.9853, "budget_tier": 1, "best_months": [11, 12, 1, 2],
     "tags": ["culture", "nature", "budget", "nomad", "wellness", "hiking", "food"],
     "blurb": "Walled northern town, mountain temples, and a large remote-work scene."},
    {"city": "Bali", "country": "Indonesia", "iso2": "ID", "region": "South-eastern Asia",
     "lat": -8.4095, "lon": 115.1889, "budget_tier": 1, "best_months": [5, 6, 7, 8, 9],
     "tags": ["beach", "surfing", "wellness", "diving", "budget", "nomad", "romantic"],
     "blurb": "Rice terraces, reef breaks and yoga; dry season runs May to September."},
    {"city": "Hanoi", "country": "Vietnam", "iso2": "VN", "region": "South-eastern Asia",
     "lat": 21.0278, "lon": 105.8342, "budget_tier": 1, "best_months": [10, 11, 3, 4],
     "tags": ["food", "history", "culture", "budget", "photography"],
     "blurb": "Old Quarter chaos, exceptional street food, Ha Long Bay a morning away."},
    {"city": "Singapore", "country": "Singapore", "iso2": "SG", "region": "South-eastern Asia",
     "lat": 1.3521, "lon": 103.8198, "budget_tier": 4, "best_months": [2, 3, 6, 7, 8],
     "tags": ["food", "family", "architecture", "shopping", "nature", "luxury"],
     "blurb": "Hawker centres and engineered gardens; hot and humid year round."},
    {"city": "Kuala Lumpur", "country": "Malaysia", "iso2": "MY", "region": "South-eastern Asia",
     "lat": 3.1390, "lon": 101.6869, "budget_tier": 1, "best_months": [2, 3, 6, 7, 8],
     "tags": ["food", "shopping", "budget", "architecture", "culture", "nomad"],
     "blurb": "Three cuisines in one city, and the cheapest long-haul hub in the region."},
    {"city": "Kathmandu", "country": "Nepal", "iso2": "NP", "region": "Southern Asia",
     "lat": 27.7172, "lon": 85.3240, "budget_tier": 1, "best_months": [3, 4, 10, 11],
     "tags": ["hiking", "mountains", "adventure", "culture", "budget", "photography"],
     "blurb": "Trekking gateway; October-November and March-April are the clear windows."},
    {"city": "Jaipur", "country": "India", "iso2": "IN", "region": "Southern Asia",
     "lat": 26.9124, "lon": 75.7873, "budget_tier": 1, "best_months": [11, 12, 1, 2],
     "tags": ["history", "architecture", "culture", "shopping", "budget", "photography"],
     "blurb": "Forts, stepwells and a pink old city; unbearable from April to June."},
    {"city": "Colombo", "country": "Sri Lanka", "iso2": "LK", "region": "Southern Asia",
     "lat": 6.9271, "lon": 79.8612, "budget_tier": 1, "best_months": [1, 2, 3, 12],
     "tags": ["beach", "wildlife", "food", "budget", "surfing", "nature"],
     "blurb": "Compact island: tea hills, safari parks and two coasts with opposite monsoons."},

    # ---------------- Africa ----------------
    {"city": "Marrakesh", "country": "Morocco", "iso2": "MA", "region": "Northern Africa",
     "lat": 31.6295, "lon": -7.9811, "budget_tier": 1, "best_months": [3, 4, 5, 10, 11],
     "tags": ["culture", "food", "shopping", "desert", "budget", "photography", "wellness"],
     "blurb": "Medina, riads and the Atlas within reach; skip July and August."},
    {"city": "Cape Town", "country": "South Africa", "iso2": "ZA", "region": "Southern Africa",
     "lat": -33.9249, "lon": 18.4241, "budget_tier": 2, "best_months": [10, 11, 12, 1, 2, 3],
     "tags": ["beach", "hiking", "wine", "nature", "wildlife", "adventure", "food"],
     "blurb": "A mountain in the middle of a coastal city, wine farms 40 minutes out."},
    {"city": "Zanzibar", "country": "Tanzania", "iso2": "TZ", "region": "Sub-Saharan Africa",
     "lat": -6.1659, "lon": 39.2026, "budget_tier": 2, "best_months": [6, 7, 8, 9, 1, 2],
     "tags": ["beach", "diving", "islands", "romantic", "history", "wellness"],
     "blurb": "Stone Town history plus reef and sandbank beaches on the east coast."},
    {"city": "Nairobi", "country": "Kenya", "iso2": "KE", "region": "Sub-Saharan Africa",
     "lat": -1.2921, "lon": 36.8219, "budget_tier": 2, "best_months": [1, 2, 6, 7, 8, 9],
     "tags": ["wildlife", "nature", "adventure", "photography", "budget"],
     "blurb": "Safari gateway; the Mara migration peaks July to September."},

    # ---------------- Americas ----------------
    {"city": "New York", "country": "United States", "iso2": "US", "region": "Northern America",
     "lat": 40.7128, "lon": -74.0060, "budget_tier": 4, "best_months": [4, 5, 6, 9, 10, 12],
     "tags": ["museums", "food", "nightlife", "shopping", "art", "architecture"],
     "blurb": "Maximum density of everything; shoulder seasons are the kind ones."},
    {"city": "Mexico City", "country": "Mexico", "iso2": "MX", "region": "Central America",
     "lat": 19.4326, "lon": -99.1332, "budget_tier": 1, "best_months": [3, 4, 5, 10, 11],
     "tags": ["food", "art", "history", "museums", "budget", "nightlife", "culture"],
     "blurb": "Aztec ruins under a Baroque grid, and arguably the best food city alive."},
    {"city": "Oaxaca", "country": "Mexico", "iso2": "MX", "region": "Central America",
     "lat": 17.0732, "lon": -96.7266, "budget_tier": 1, "best_months": [10, 11, 3, 4],
     "tags": ["food", "culture", "festival", "budget", "art", "photography"],
     "blurb": "Mole, mezcal and Day of the Dead; ruins of Monte Alban above town."},
    {"city": "Cusco", "country": "Peru", "iso2": "PE", "region": "South America",
     "lat": -13.5320, "lon": -71.9675, "budget_tier": 1, "best_months": [5, 6, 7, 8, 9],
     "tags": ["history", "hiking", "mountains", "adventure", "culture", "budget"],
     "blurb": "Inca capital at 3,400 m and the only sane base for Machu Picchu."},
    {"city": "Rio de Janeiro", "country": "Brazil", "iso2": "BR", "region": "South America",
     "lat": -22.9068, "lon": -43.1729, "budget_tier": 2, "best_months": [5, 6, 9, 10, 11],
     "tags": ["beach", "nightlife", "nature", "hiking", "festival", "photography"],
     "blurb": "Granite peaks dropping straight into city beaches."},
    {"city": "Buenos Aires", "country": "Argentina", "iso2": "AR", "region": "South America",
     "lat": -34.6037, "lon": -58.3816, "budget_tier": 1, "best_months": [3, 4, 10, 11],
     "tags": ["food", "nightlife", "culture", "architecture", "wine", "budget", "art"],
     "blurb": "European bones, Latin pulse, steak and tango; excellent value."},
    {"city": "Medellin", "country": "Colombia", "iso2": "CO", "region": "South America",
     "lat": 6.2442, "lon": -75.5812, "budget_tier": 1, "best_months": [1, 2, 3, 7, 8, 12],
     "tags": ["nomad", "nightlife", "nature", "budget", "culture", "food"],
     "blurb": "Spring-like all year at 1,500 m, with cable cars for public transit."},
    {"city": "Vancouver", "country": "Canada", "iso2": "CA", "region": "Northern America",
     "lat": 49.2827, "lon": -123.1207, "budget_tier": 3, "best_months": [6, 7, 8, 9],
     "tags": ["nature", "hiking", "mountains", "skiing", "food", "family", "cycling"],
     "blurb": "Ocean, rainforest and ski slopes inside one metro area."},

    # ---------------- Oceania ----------------
    {"city": "Sydney", "country": "Australia", "iso2": "AU", "region": "Australia and New Zealand",
     "lat": -33.8688, "lon": 151.2093, "budget_tier": 4, "best_months": [10, 11, 12, 2, 3, 4],
     "tags": ["beach", "surfing", "food", "nature", "family", "architecture"],
     "blurb": "Harbour city with genuine surf beaches on the commuter network."},
    {"city": "Queenstown", "country": "New Zealand", "iso2": "NZ",
     "region": "Australia and New Zealand",
     "lat": -45.0312, "lon": 168.6626, "budget_tier": 3, "best_months": [12, 1, 2, 3, 7, 8],
     "tags": ["adventure", "hiking", "skiing", "lakes", "mountains", "nature", "photography"],
     "blurb": "Adventure-sport capital between an alpine lake and the Remarkables."},
)


def _tier_from_budget(per_day_eur: float | None) -> int | None:
    """Map a per-person daily budget onto the catalogue's coarse tier scale."""
    if per_day_eur is None:
        return None
    if per_day_eur < 60:
        return 1
    if per_day_eur < 130:
        return 2
    if per_day_eur < 240:
        return 3
    return 4


def filter_destinations(
    interests: Iterable[str] | None = None,
    month: int | None = None,
    max_budget_tier: int | None = None,
    regions: Iterable[str] | None = None,
    exclude_countries: Iterable[str] | None = None,
    exclude_cities: Iterable[str] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Rank catalogue entries against a traveller's stated preferences.

    Scoring is intentionally transparent -- interest overlap dominates, season
    fit is a strong secondary signal, and budget fit acts mostly as a filter.
    The returned ``match`` block is surfaced to the user as the "why this
    destination" explanation, so it must stay explainable.
    """
    wanted = {t.strip().lower() for t in (interests or []) if t and t.strip()}
    region_set = {r.strip() for r in (regions or []) if r and r.strip()}
    blocked_countries = {c.strip().lower() for c in (exclude_countries or []) if c}
    blocked_cities = {c.strip().lower() for c in (exclude_cities or []) if c}

    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in DESTINATIONS:
        if entry["city"].lower() in blocked_cities:
            continue
        if entry["country"].lower() in blocked_countries:
            continue
        if region_set and entry["region"] not in region_set:
            continue
        if max_budget_tier is not None and entry["budget_tier"] > max_budget_tier:
            continue

        tags = set(entry["tags"])
        overlap = sorted(wanted & tags)
        # Interest match: fraction of the traveller's interests that are met.
        interest_score = len(overlap) / len(wanted) if wanted else 0.5

        in_season = month in entry["best_months"] if month else None
        if month is None:
            season_score = 0.6
        elif in_season:
            season_score = 1.0
        else:
            # Adjacent months are a near-miss, not a disqualification.
            adjacent = any(
                abs(((month - m + 6) % 12) - 6) <= 1 for m in entry["best_months"]
            )
            season_score = 0.55 if adjacent else 0.18

        budget_score = 1.0
        if max_budget_tier is not None:
            # Reward headroom: a tier-1 city under a tier-3 budget is a bargain.
            budget_score = 1.0 - 0.12 * (entry["budget_tier"] - 1)

        total = 0.55 * interest_score + 0.33 * season_score + 0.12 * budget_score

        candidate = dict(entry)
        candidate["match"] = {
            "score": round(total * 100, 1),
            "matched_interests": overlap,
            "in_best_season": in_season,
            "interest_score": round(interest_score * 100, 1),
            "season_score": round(season_score * 100, 1),
        }
        scored.append((total, candidate))

    # Stable tie-break on city name keeps output deterministic for tests.
    scored.sort(key=lambda pair: (-pair[0], pair[1]["city"]))
    return [entry for _, entry in scored[: max(1, limit)]]


def find_destination(name: str) -> dict[str, Any] | None:
    """Look up a catalogue entry by city name (case-insensitive)."""
    needle = name.strip().lower()
    for entry in DESTINATIONS:
        if entry["city"].lower() == needle:
            return dict(entry)
    for entry in DESTINATIONS:
        if needle in entry["city"].lower() or entry["city"].lower() in needle:
            return dict(entry)
    return None


def budget_tier_for(per_day_eur: float | None) -> int | None:
    return _tier_from_budget(per_day_eur)
