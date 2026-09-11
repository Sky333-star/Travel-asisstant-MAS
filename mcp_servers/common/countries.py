"""Bundled country reference data.

WHY THIS IS BUNDLED RATHER THAN FETCHED
---------------------------------------
This project originally used restcountries.com for country facts. During
development that service deprecated its open versions and moved to
``api.restcountries.com``, which now answers keyless requests with
``{"errors":[{"message":"Authorization key required."}]}``. That breaks the
project's hard constraint of using only keyless sources.

Bundling the data is the better answer regardless:

* The facts here -- currency, languages, driving side, calling code, primary
  timezone -- change on a scale of years, not minutes. There is nothing to
  keep fresh.
* It removes a network call, a rate limit and a failure mode from the
  critical path of every plan.
* It is genuinely free forever, with no provider able to revoke it.

Coverage is every country in the destination catalogue plus the most-travelled
countries worldwide. Lookups fall back gracefully: an unknown country returns
``found: False`` rather than a wrong guess.

Sources: ISO 3166-1 (codes), ISO 4217 (currencies), ITU-T E.164 (calling
codes), IANA tz database (timezones). Compiled 2026.
"""

from __future__ import annotations

from typing import Any

# (iso2, iso3, name, capital, region, subregion, currency_code, currency_name,
#  currency_symbol, languages, primary_timezone, drives_on, calling_code)
_ROWS: tuple[tuple, ...] = (
    # ---- Europe ----
    ("PT", "PRT", "Portugal", "Lisbon", "Europe", "Southern Europe", "EUR", "Euro", "€", ("Portuguese",), "Europe/Lisbon", "right", "+351"),
    ("ES", "ESP", "Spain", "Madrid", "Europe", "Southern Europe", "EUR", "Euro", "€", ("Spanish",), "Europe/Madrid", "right", "+34"),
    ("IT", "ITA", "Italy", "Rome", "Europe", "Southern Europe", "EUR", "Euro", "€", ("Italian",), "Europe/Rome", "right", "+39"),
    ("GR", "GRC", "Greece", "Athens", "Europe", "Southern Europe", "EUR", "Euro", "€", ("Greek",), "Europe/Athens", "right", "+30"),
    ("HR", "HRV", "Croatia", "Zagreb", "Europe", "Southern Europe", "EUR", "Euro", "€", ("Croatian",), "Europe/Zagreb", "right", "+385"),
    ("MT", "MLT", "Malta", "Valletta", "Europe", "Southern Europe", "EUR", "Euro", "€", ("Maltese", "English"), "Europe/Malta", "left", "+356"),
    ("SI", "SVN", "Slovenia", "Ljubljana", "Europe", "Central Europe", "EUR", "Euro", "€", ("Slovene",), "Europe/Ljubljana", "right", "+386"),
    ("CY", "CYP", "Cyprus", "Nicosia", "Europe", "Southern Europe", "EUR", "Euro", "€", ("Greek", "Turkish"), "Asia/Nicosia", "left", "+357"),
    ("FR", "FRA", "France", "Paris", "Europe", "Western Europe", "EUR", "Euro", "€", ("French",), "Europe/Paris", "right", "+33"),
    ("DE", "DEU", "Germany", "Berlin", "Europe", "Western Europe", "EUR", "Euro", "€", ("German",), "Europe/Berlin", "right", "+49"),
    ("NL", "NLD", "Netherlands", "Amsterdam", "Europe", "Western Europe", "EUR", "Euro", "€", ("Dutch",), "Europe/Amsterdam", "right", "+31"),
    ("BE", "BEL", "Belgium", "Brussels", "Europe", "Western Europe", "EUR", "Euro", "€", ("Dutch", "French", "German"), "Europe/Brussels", "right", "+32"),
    ("LU", "LUX", "Luxembourg", "Luxembourg", "Europe", "Western Europe", "EUR", "Euro", "€", ("Luxembourgish", "French", "German"), "Europe/Luxembourg", "right", "+352"),
    ("AT", "AUT", "Austria", "Vienna", "Europe", "Central Europe", "EUR", "Euro", "€", ("German",), "Europe/Vienna", "right", "+43"),
    ("CH", "CHE", "Switzerland", "Bern", "Europe", "Western Europe", "CHF", "Swiss franc", "CHF", ("German", "French", "Italian", "Romansh"), "Europe/Zurich", "right", "+41"),
    ("CZ", "CZE", "Czechia", "Prague", "Europe", "Central Europe", "CZK", "Czech koruna", "Kč", ("Czech",), "Europe/Prague", "right", "+420"),
    ("SK", "SVK", "Slovakia", "Bratislava", "Europe", "Central Europe", "EUR", "Euro", "€", ("Slovak",), "Europe/Bratislava", "right", "+421"),
    ("HU", "HUN", "Hungary", "Budapest", "Europe", "Eastern Europe", "HUF", "Hungarian forint", "Ft", ("Hungarian",), "Europe/Budapest", "right", "+36"),
    ("PL", "POL", "Poland", "Warsaw", "Europe", "Eastern Europe", "PLN", "Polish złoty", "zł", ("Polish",), "Europe/Warsaw", "right", "+48"),
    ("RO", "ROU", "Romania", "Bucharest", "Europe", "Eastern Europe", "RON", "Romanian leu", "lei", ("Romanian",), "Europe/Bucharest", "right", "+40"),
    ("BG", "BGR", "Bulgaria", "Sofia", "Europe", "Eastern Europe", "BGN", "Bulgarian lev", "лв", ("Bulgarian",), "Europe/Sofia", "right", "+359"),
    ("RS", "SRB", "Serbia", "Belgrade", "Europe", "Southern Europe", "RSD", "Serbian dinar", "дин.", ("Serbian",), "Europe/Belgrade", "right", "+381"),
    ("BA", "BIH", "Bosnia and Herzegovina", "Sarajevo", "Europe", "Southern Europe", "BAM", "Convertible mark", "KM", ("Bosnian", "Croatian", "Serbian"), "Europe/Sarajevo", "right", "+387"),
    ("ME", "MNE", "Montenegro", "Podgorica", "Europe", "Southern Europe", "EUR", "Euro", "€", ("Montenegrin",), "Europe/Podgorica", "right", "+382"),
    ("AL", "ALB", "Albania", "Tirana", "Europe", "Southern Europe", "ALL", "Albanian lek", "L", ("Albanian",), "Europe/Tirane", "right", "+355"),
    ("MK", "MKD", "North Macedonia", "Skopje", "Europe", "Southern Europe", "MKD", "Macedonian denar", "ден", ("Macedonian",), "Europe/Skopje", "right", "+389"),
    ("DK", "DNK", "Denmark", "Copenhagen", "Europe", "Northern Europe", "DKK", "Danish krone", "kr", ("Danish",), "Europe/Copenhagen", "right", "+45"),
    ("SE", "SWE", "Sweden", "Stockholm", "Europe", "Northern Europe", "SEK", "Swedish krona", "kr", ("Swedish",), "Europe/Stockholm", "right", "+46"),
    ("NO", "NOR", "Norway", "Oslo", "Europe", "Northern Europe", "NOK", "Norwegian krone", "kr", ("Norwegian",), "Europe/Oslo", "right", "+47"),
    ("FI", "FIN", "Finland", "Helsinki", "Europe", "Northern Europe", "EUR", "Euro", "€", ("Finnish", "Swedish"), "Europe/Helsinki", "right", "+358"),
    ("IS", "ISL", "Iceland", "Reykjavik", "Europe", "Northern Europe", "ISK", "Icelandic króna", "kr", ("Icelandic",), "Atlantic/Reykjavik", "right", "+354"),
    ("EE", "EST", "Estonia", "Tallinn", "Europe", "Northern Europe", "EUR", "Euro", "€", ("Estonian",), "Europe/Tallinn", "right", "+372"),
    ("LV", "LVA", "Latvia", "Riga", "Europe", "Northern Europe", "EUR", "Euro", "€", ("Latvian",), "Europe/Riga", "right", "+371"),
    ("LT", "LTU", "Lithuania", "Vilnius", "Europe", "Northern Europe", "EUR", "Euro", "€", ("Lithuanian",), "Europe/Vilnius", "right", "+370"),
    ("GB", "GBR", "United Kingdom", "London", "Europe", "Northern Europe", "GBP", "Pound sterling", "£", ("English",), "Europe/London", "left", "+44"),
    ("IE", "IRL", "Ireland", "Dublin", "Europe", "Northern Europe", "EUR", "Euro", "€", ("English", "Irish"), "Europe/Dublin", "left", "+353"),
    ("UA", "UKR", "Ukraine", "Kyiv", "Europe", "Eastern Europe", "UAH", "Ukrainian hryvnia", "₴", ("Ukrainian",), "Europe/Kyiv", "right", "+380"),

    # ---- Western & Central Asia ----
    ("TR", "TUR", "Turkiye", "Ankara", "Asia", "Western Asia", "TRY", "Turkish lira", "₺", ("Turkish",), "Europe/Istanbul", "right", "+90"),
    ("GE", "GEO", "Georgia", "Tbilisi", "Asia", "Western Asia", "GEL", "Georgian lari", "₾", ("Georgian",), "Asia/Tbilisi", "right", "+995"),
    ("AM", "ARM", "Armenia", "Yerevan", "Asia", "Western Asia", "AMD", "Armenian dram", "֏", ("Armenian",), "Asia/Yerevan", "right", "+374"),
    ("AZ", "AZE", "Azerbaijan", "Baku", "Asia", "Western Asia", "AZN", "Azerbaijani manat", "₼", ("Azerbaijani",), "Asia/Baku", "right", "+994"),
    ("AE", "ARE", "United Arab Emirates", "Abu Dhabi", "Asia", "Western Asia", "AED", "UAE dirham", "د.إ", ("Arabic",), "Asia/Dubai", "right", "+971"),
    ("QA", "QAT", "Qatar", "Doha", "Asia", "Western Asia", "QAR", "Qatari riyal", "ر.ق", ("Arabic",), "Asia/Qatar", "right", "+974"),
    ("SA", "SAU", "Saudi Arabia", "Riyadh", "Asia", "Western Asia", "SAR", "Saudi riyal", "ر.س", ("Arabic",), "Asia/Riyadh", "right", "+966"),
    ("OM", "OMN", "Oman", "Muscat", "Asia", "Western Asia", "OMR", "Omani rial", "ر.ع.", ("Arabic",), "Asia/Muscat", "right", "+968"),
    ("KW", "KWT", "Kuwait", "Kuwait City", "Asia", "Western Asia", "KWD", "Kuwaiti dinar", "د.ك", ("Arabic",), "Asia/Kuwait", "right", "+965"),
    ("BH", "BHR", "Bahrain", "Manama", "Asia", "Western Asia", "BHD", "Bahraini dinar", ".د.ب", ("Arabic",), "Asia/Bahrain", "right", "+973"),
    ("JO", "JOR", "Jordan", "Amman", "Asia", "Western Asia", "JOD", "Jordanian dinar", "د.ا", ("Arabic",), "Asia/Amman", "right", "+962"),
    ("IL", "ISR", "Israel", "Jerusalem", "Asia", "Western Asia", "ILS", "Israeli new shekel", "₪", ("Hebrew", "Arabic"), "Asia/Jerusalem", "right", "+972"),
    ("LB", "LBN", "Lebanon", "Beirut", "Asia", "Western Asia", "LBP", "Lebanese pound", "ل.ل", ("Arabic",), "Asia/Beirut", "right", "+961"),
    ("KZ", "KAZ", "Kazakhstan", "Astana", "Asia", "Central Asia", "KZT", "Kazakhstani tenge", "₸", ("Kazakh", "Russian"), "Asia/Almaty", "right", "+7"),
    ("UZ", "UZB", "Uzbekistan", "Tashkent", "Asia", "Central Asia", "UZS", "Uzbekistani som", "so'm", ("Uzbek",), "Asia/Tashkent", "right", "+998"),

    # ---- South & East Asia ----
    ("JP", "JPN", "Japan", "Tokyo", "Asia", "Eastern Asia", "JPY", "Japanese yen", "¥", ("Japanese",), "Asia/Tokyo", "left", "+81"),
    ("KR", "KOR", "South Korea", "Seoul", "Asia", "Eastern Asia", "KRW", "South Korean won", "₩", ("Korean",), "Asia/Seoul", "right", "+82"),
    ("CN", "CHN", "China", "Beijing", "Asia", "Eastern Asia", "CNY", "Chinese yuan", "¥", ("Chinese",), "Asia/Shanghai", "right", "+86"),
    ("TW", "TWN", "Taiwan", "Taipei", "Asia", "Eastern Asia", "TWD", "New Taiwan dollar", "NT$", ("Chinese",), "Asia/Taipei", "right", "+886"),
    ("HK", "HKG", "Hong Kong", "Hong Kong", "Asia", "Eastern Asia", "HKD", "Hong Kong dollar", "HK$", ("Chinese", "English"), "Asia/Hong_Kong", "left", "+852"),
    ("MN", "MNG", "Mongolia", "Ulaanbaatar", "Asia", "Eastern Asia", "MNT", "Mongolian tögrög", "₮", ("Mongolian",), "Asia/Ulaanbaatar", "right", "+976"),
    ("TH", "THA", "Thailand", "Bangkok", "Asia", "South-eastern Asia", "THB", "Thai baht", "฿", ("Thai",), "Asia/Bangkok", "left", "+66"),
    ("VN", "VNM", "Vietnam", "Hanoi", "Asia", "South-eastern Asia", "VND", "Vietnamese dong", "₫", ("Vietnamese",), "Asia/Ho_Chi_Minh", "right", "+84"),
    ("SG", "SGP", "Singapore", "Singapore", "Asia", "South-eastern Asia", "SGD", "Singapore dollar", "S$", ("English", "Malay", "Chinese", "Tamil"), "Asia/Singapore", "left", "+65"),
    ("MY", "MYS", "Malaysia", "Kuala Lumpur", "Asia", "South-eastern Asia", "MYR", "Malaysian ringgit", "RM", ("Malay",), "Asia/Kuala_Lumpur", "left", "+60"),
    ("ID", "IDN", "Indonesia", "Jakarta", "Asia", "South-eastern Asia", "IDR", "Indonesian rupiah", "Rp", ("Indonesian",), "Asia/Jakarta", "left", "+62"),
    ("PH", "PHL", "Philippines", "Manila", "Asia", "South-eastern Asia", "PHP", "Philippine peso", "₱", ("Filipino", "English"), "Asia/Manila", "right", "+63"),
    ("KH", "KHM", "Cambodia", "Phnom Penh", "Asia", "South-eastern Asia", "KHR", "Cambodian riel", "៛", ("Khmer",), "Asia/Phnom_Penh", "right", "+855"),
    ("LA", "LAO", "Laos", "Vientiane", "Asia", "South-eastern Asia", "LAK", "Lao kip", "₭", ("Lao",), "Asia/Vientiane", "right", "+856"),
    ("MM", "MMR", "Myanmar", "Naypyidaw", "Asia", "South-eastern Asia", "MMK", "Burmese kyat", "Ks", ("Burmese",), "Asia/Yangon", "right", "+95"),
    ("IN", "IND", "India", "New Delhi", "Asia", "Southern Asia", "INR", "Indian rupee", "₹", ("Hindi", "English"), "Asia/Kolkata", "left", "+91"),
    ("NP", "NPL", "Nepal", "Kathmandu", "Asia", "Southern Asia", "NPR", "Nepalese rupee", "₨", ("Nepali",), "Asia/Kathmandu", "left", "+977"),
    ("LK", "LKA", "Sri Lanka", "Colombo", "Asia", "Southern Asia", "LKR", "Sri Lankan rupee", "Rs", ("Sinhala", "Tamil"), "Asia/Colombo", "left", "+94"),
    ("BD", "BGD", "Bangladesh", "Dhaka", "Asia", "Southern Asia", "BDT", "Bangladeshi taka", "৳", ("Bengali",), "Asia/Dhaka", "left", "+880"),
    ("PK", "PAK", "Pakistan", "Islamabad", "Asia", "Southern Asia", "PKR", "Pakistani rupee", "₨", ("Urdu", "English"), "Asia/Karachi", "left", "+92"),
    ("MV", "MDV", "Maldives", "Malé", "Asia", "Southern Asia", "MVR", "Maldivian rufiyaa", ".ރ", ("Dhivehi",), "Indian/Maldives", "left", "+960"),

    # ---- Africa ----
    ("MA", "MAR", "Morocco", "Rabat", "Africa", "Northern Africa", "MAD", "Moroccan dirham", "د.م.", ("Arabic", "Berber"), "Africa/Casablanca", "right", "+212"),
    ("EG", "EGY", "Egypt", "Cairo", "Africa", "Northern Africa", "EGP", "Egyptian pound", "£", ("Arabic",), "Africa/Cairo", "right", "+20"),
    ("TN", "TUN", "Tunisia", "Tunis", "Africa", "Northern Africa", "TND", "Tunisian dinar", "د.ت", ("Arabic",), "Africa/Tunis", "right", "+216"),
    ("DZ", "DZA", "Algeria", "Algiers", "Africa", "Northern Africa", "DZD", "Algerian dinar", "د.ج", ("Arabic",), "Africa/Algiers", "right", "+213"),
    ("ZA", "ZAF", "South Africa", "Pretoria", "Africa", "Southern Africa", "ZAR", "South African rand", "R", ("English", "Afrikaans", "Zulu"), "Africa/Johannesburg", "left", "+27"),
    ("NA", "NAM", "Namibia", "Windhoek", "Africa", "Southern Africa", "NAD", "Namibian dollar", "$", ("English",), "Africa/Windhoek", "left", "+264"),
    ("BW", "BWA", "Botswana", "Gaborone", "Africa", "Southern Africa", "BWP", "Botswana pula", "P", ("English", "Tswana"), "Africa/Gaborone", "left", "+267"),
    ("KE", "KEN", "Kenya", "Nairobi", "Africa", "Sub-Saharan Africa", "KES", "Kenyan shilling", "Sh", ("Swahili", "English"), "Africa/Nairobi", "left", "+254"),
    ("TZ", "TZA", "Tanzania", "Dodoma", "Africa", "Sub-Saharan Africa", "TZS", "Tanzanian shilling", "Sh", ("Swahili", "English"), "Africa/Dar_es_Salaam", "left", "+255"),
    ("UG", "UGA", "Uganda", "Kampala", "Africa", "Sub-Saharan Africa", "UGX", "Ugandan shilling", "Sh", ("English", "Swahili"), "Africa/Kampala", "left", "+256"),
    ("RW", "RWA", "Rwanda", "Kigali", "Africa", "Sub-Saharan Africa", "RWF", "Rwandan franc", "Fr", ("Kinyarwanda", "English", "French"), "Africa/Kigali", "right", "+250"),
    ("ET", "ETH", "Ethiopia", "Addis Ababa", "Africa", "Sub-Saharan Africa", "ETB", "Ethiopian birr", "Br", ("Amharic",), "Africa/Addis_Ababa", "right", "+251"),
    ("NG", "NGA", "Nigeria", "Abuja", "Africa", "Sub-Saharan Africa", "NGN", "Nigerian naira", "₦", ("English",), "Africa/Lagos", "right", "+234"),
    ("GH", "GHA", "Ghana", "Accra", "Africa", "Sub-Saharan Africa", "GHS", "Ghanaian cedi", "₵", ("English",), "Africa/Accra", "right", "+233"),
    ("SN", "SEN", "Senegal", "Dakar", "Africa", "Sub-Saharan Africa", "XOF", "West African CFA franc", "Fr", ("French",), "Africa/Dakar", "right", "+221"),
    ("MU", "MUS", "Mauritius", "Port Louis", "Africa", "Sub-Saharan Africa", "MUR", "Mauritian rupee", "₨", ("English", "French"), "Indian/Mauritius", "left", "+230"),
    ("SC", "SYC", "Seychelles", "Victoria", "Africa", "Sub-Saharan Africa", "SCR", "Seychellois rupee", "₨", ("English", "French", "Creole"), "Indian/Mahe", "left", "+248"),
    ("ZW", "ZWE", "Zimbabwe", "Harare", "Africa", "Sub-Saharan Africa", "USD", "United States dollar", "$", ("English", "Shona"), "Africa/Harare", "left", "+263"),
    ("ZM", "ZMB", "Zambia", "Lusaka", "Africa", "Sub-Saharan Africa", "ZMW", "Zambian kwacha", "ZK", ("English",), "Africa/Lusaka", "left", "+260"),

    # ---- Americas ----
    ("US", "USA", "United States", "Washington, D.C.", "Americas", "Northern America", "USD", "United States dollar", "$", ("English",), "America/New_York", "right", "+1"),
    ("CA", "CAN", "Canada", "Ottawa", "Americas", "Northern America", "CAD", "Canadian dollar", "$", ("English", "French"), "America/Toronto", "right", "+1"),
    ("MX", "MEX", "Mexico", "Mexico City", "Americas", "Central America", "MXN", "Mexican peso", "$", ("Spanish",), "America/Mexico_City", "right", "+52"),
    ("GT", "GTM", "Guatemala", "Guatemala City", "Americas", "Central America", "GTQ", "Guatemalan quetzal", "Q", ("Spanish",), "America/Guatemala", "right", "+502"),
    ("CR", "CRI", "Costa Rica", "San José", "Americas", "Central America", "CRC", "Costa Rican colón", "₡", ("Spanish",), "America/Costa_Rica", "right", "+506"),
    ("PA", "PAN", "Panama", "Panama City", "Americas", "Central America", "PAB", "Panamanian balboa", "B/.", ("Spanish",), "America/Panama", "right", "+507"),
    ("CU", "CUB", "Cuba", "Havana", "Americas", "Caribbean", "CUP", "Cuban peso", "$", ("Spanish",), "America/Havana", "right", "+53"),
    ("DO", "DOM", "Dominican Republic", "Santo Domingo", "Americas", "Caribbean", "DOP", "Dominican peso", "$", ("Spanish",), "America/Santo_Domingo", "right", "+1"),
    ("JM", "JAM", "Jamaica", "Kingston", "Americas", "Caribbean", "JMD", "Jamaican dollar", "$", ("English",), "America/Jamaica", "left", "+1"),
    ("BS", "BHS", "Bahamas", "Nassau", "Americas", "Caribbean", "BSD", "Bahamian dollar", "$", ("English",), "America/Nassau", "left", "+1"),
    ("TT", "TTO", "Trinidad and Tobago", "Port of Spain", "Americas", "Caribbean", "TTD", "Trinidad and Tobago dollar", "$", ("English",), "America/Port_of_Spain", "left", "+1"),
    ("BR", "BRA", "Brazil", "Brasília", "Americas", "South America", "BRL", "Brazilian real", "R$", ("Portuguese",), "America/Sao_Paulo", "right", "+55"),
    ("AR", "ARG", "Argentina", "Buenos Aires", "Americas", "South America", "ARS", "Argentine peso", "$", ("Spanish",), "America/Argentina/Buenos_Aires", "right", "+54"),
    ("CL", "CHL", "Chile", "Santiago", "Americas", "South America", "CLP", "Chilean peso", "$", ("Spanish",), "America/Santiago", "right", "+56"),
    ("PE", "PER", "Peru", "Lima", "Americas", "South America", "PEN", "Peruvian sol", "S/", ("Spanish",), "America/Lima", "right", "+51"),
    ("CO", "COL", "Colombia", "Bogotá", "Americas", "South America", "COP", "Colombian peso", "$", ("Spanish",), "America/Bogota", "right", "+57"),
    ("EC", "ECU", "Ecuador", "Quito", "Americas", "South America", "USD", "United States dollar", "$", ("Spanish",), "America/Guayaquil", "right", "+593"),
    ("BO", "BOL", "Bolivia", "Sucre", "Americas", "South America", "BOB", "Bolivian boliviano", "Bs.", ("Spanish",), "America/La_Paz", "right", "+591"),
    ("UY", "URY", "Uruguay", "Montevideo", "Americas", "South America", "UYU", "Uruguayan peso", "$", ("Spanish",), "America/Montevideo", "right", "+598"),
    ("PY", "PRY", "Paraguay", "Asunción", "Americas", "South America", "PYG", "Paraguayan guaraní", "₲", ("Spanish", "Guarani"), "America/Asuncion", "right", "+595"),

    # ---- Oceania ----
    ("AU", "AUS", "Australia", "Canberra", "Oceania", "Australia and New Zealand", "AUD", "Australian dollar", "$", ("English",), "Australia/Sydney", "left", "+61"),
    ("NZ", "NZL", "New Zealand", "Wellington", "Oceania", "Australia and New Zealand", "NZD", "New Zealand dollar", "$", ("English", "Maori"), "Pacific/Auckland", "left", "+64"),
    ("FJ", "FJI", "Fiji", "Suva", "Oceania", "Melanesia", "FJD", "Fijian dollar", "$", ("English", "Fijian"), "Pacific/Fiji", "left", "+679"),
    ("PG", "PNG", "Papua New Guinea", "Port Moresby", "Oceania", "Melanesia", "PGK", "Papua New Guinean kina", "K", ("English", "Tok Pisin"), "Pacific/Port_Moresby", "left", "+675"),
)

_FIELDS = (
    "iso2", "iso3", "name", "capital", "region", "subregion", "currency_code",
    "currency_name", "currency_symbol", "languages", "timezone", "drives_on",
    "calling_code",
)

COUNTRIES: dict[str, dict[str, Any]] = {}
_BY_NAME: dict[str, str] = {}
_BY_ISO3: dict[str, str] = {}

for _row in _ROWS:
    _record = dict(zip(_FIELDS, _row, strict=True))
    _record["languages"] = list(_record["languages"])
    COUNTRIES[_record["iso2"]] = _record
    _BY_NAME[_record["name"].lower()] = _record["iso2"]
    _BY_ISO3[_record["iso3"]] = _record["iso2"]

# Common alternative names and spellings travellers actually type.
_ALIASES: dict[str, str] = {
    "usa": "US", "u.s.": "US", "u.s.a.": "US", "america": "US",
    "united states of america": "US", "the united states": "US",
    "uk": "GB", "great britain": "GB", "britain": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "turkey": "TR", "türkiye": "TR",
    "czech republic": "CZ", "holland": "NL", "the netherlands": "NL",
    "south korea": "KR", "korea": "KR", "republic of korea": "KR",
    "uae": "AE", "emirates": "AE", "dubai": "AE", "abu dhabi": "AE",
    "vietnam": "VN", "viet nam": "VN", "burma": "MM",
    "ivory coast": "SN", "swaziland": "ZA", "macedonia": "MK",
    "bosnia": "BA", "cape verde": "SN",
    "hong kong sar": "HK", "prc": "CN", "mainland china": "CN",
}


def lookup_country(query: str) -> dict[str, Any] | None:
    """Find a country by ISO 3166-1 alpha-2/alpha-3 code, name, or alias."""
    if not query:
        return None
    raw = query.strip()
    lowered = raw.lower()

    if len(raw) == 2 and raw.upper() in COUNTRIES:
        return dict(COUNTRIES[raw.upper()])
    if len(raw) == 3 and raw.upper() in _BY_ISO3:
        return dict(COUNTRIES[_BY_ISO3[raw.upper()]])
    if lowered in _BY_NAME:
        return dict(COUNTRIES[_BY_NAME[lowered]])
    if lowered in _ALIASES:
        return dict(COUNTRIES[_ALIASES[lowered]])

    # Last resort: unambiguous partial match only. Returning the wrong country
    # is worse than returning nothing, so an ambiguous prefix is rejected.
    matches = [iso for name, iso in _BY_NAME.items() if lowered in name]
    if len(matches) == 1:
        return dict(COUNTRIES[matches[0]])
    return None


def currency_for(country: str) -> str | None:
    """ISO 4217 currency code for a country, or ``None`` if unknown."""
    record = lookup_country(country)
    return record["currency_code"] if record else None
