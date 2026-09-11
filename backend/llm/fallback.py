"""Deterministic, rule-based understanding of a travel request.

This module is the reason the system has no hard dependency on an LLM. It
parses a free-text request with regular expressions and keyword tables into the
same :class:`TravelBrief` the LLM path produces. The result is less nuanced --
it will not infer that "our anniversary" implies a romantic trip for two the
way a good model does -- but it is predictable, instant, free, and it never
hallucinates a date.

It serves three distinct purposes:

1. **Zero-key operation.** With no ``HUGGINGFACE_API_KEY`` the whole graph
   still runs end to end.
2. **A floor under the LLM.** The LLM's output is merged *over* these results,
   so a model that omits a field the regex found still gets a complete brief.
3. **A test fixture.** Deterministic parsing makes graph tests reproducible
   without mocking a network call.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from functools import lru_cache

from ..schemas.travel import (
    Budget,
    ClimatePreference,
    Constraints,
    DateWindow,
    PartyComposition,
    TravelBrief,
    TravelStyle,
    TripPace,
)

# --------------------------------------------------------------------------
# Vocabulary tables
# --------------------------------------------------------------------------

INTEREST_KEYWORDS: dict[str, tuple[str, ...]] = {
    "beach": ("beach", "beaches", "seaside", "coast", "sand", "sea", "swim", "swimming"),
    "culture": ("culture", "cultural", "local life", "traditions", "heritage"),
    "history": ("history", "historic", "historical", "ancient", "ruins", "castle",
                "archaeolog", "medieval", "roman"),
    "museums": ("museum", "museums", "exhibition", "galleries"),
    "art": ("art", "gallery", "galleries", "painting", "sculpture", "street art"),
    "food": ("food", "cuisine", "culinary", "restaurant", "eat", "eating", "foodie",
             "gastronom", "street food", "michelin"),
    "wine": ("wine", "winery", "vineyard", "wineries", "tasting"),
    "nightlife": ("nightlife", "clubs", "clubbing", "bars", "party", "partying"),
    "hiking": ("hiking", "hike", "trek", "trekking", "trail", "trails", "walking route"),
    "mountains": ("mountain", "mountains", "alpine", "alps", "peaks", "summit"),
    "nature": ("nature", "outdoors", "scenery", "scenic", "landscape", "national park",
               "forest", "waterfall"),
    "wildlife": ("wildlife", "safari", "animals", "birdwatching", "whale"),
    "diving": ("diving", "dive", "snorkel", "snorkelling", "scuba", "reef"),
    "surfing": ("surf", "surfing", "waves"),
    "skiing": ("ski", "skiing", "snowboard", "snowboarding", "slopes", "piste"),
    "architecture": ("architecture", "architectural", "cathedral", "buildings", "gaudi"),
    "shopping": ("shopping", "shop", "markets", "boutique", "mall"),
    "romantic": ("romantic", "honeymoon", "anniversary", "couple", "proposal"),
    "family": ("family", "kids", "children", "child-friendly", "toddler"),
    "adventure": ("adventure", "adrenaline", "rafting", "climbing", "bungee", "kayak",
                  "zipline", "paragliding"),
    # NB: no bare "relax" here -- "relaxed pace" is a pace signal, not a
    # request for spas. Pace and interest must not read the same word.
    "wellness": ("wellness", "spa", "yoga", "retreat", "thermal", "onsen",
                 "hot spring", "massage", "unwind at a spa"),
    "islands": ("island", "islands", "archipelago"),
    "photography": ("photography", "photo", "instagram", "views", "viewpoint"),
    "nomad": ("remote work", "digital nomad", "workation", "coworking", "wifi"),
    "festival": ("festival", "carnival", "concert", "event"),
    "desert": ("desert", "dunes", "sahara"),
    "lakes": ("lake", "lakes"),
    "cycling": ("cycling", "bike", "biking", "bicycle"),
    "roadtrip": ("road trip", "roadtrip", "driving", "self-drive", "rental car"),
    "budget": ("budget", "cheap", "affordable", "backpack", "backpacking", "hostel"),
    "luxury": ("luxury", "luxurious", "five star", "5-star", "upscale", "premium"),
}

CLIMATE_KEYWORDS: dict[ClimatePreference, tuple[str, ...]] = {
    ClimatePreference.WARM: ("warm", "hot", "sunny", "sunshine", "tropical", "heat",
                             "sun", "beach weather"),
    ClimatePreference.COOL: ("cool", "cold", "crisp", "mild cold", "chilly", "autumn",
                             "fall weather"),
    ClimatePreference.SNOW: ("snow", "snowy", "skiing", "ski", "winter wonderland",
                             "northern lights", "aurora"),
    ClimatePreference.DRY: ("dry", "no rain", "arid", "not rainy", "without rain"),
    ClimatePreference.MILD: ("mild", "temperate", "pleasant", "not too hot", "comfortable"),
}

STYLE_KEYWORDS: dict[TravelStyle, tuple[str, ...]] = {
    TravelStyle.BUDGET: ("budget", "cheap", "affordable", "shoestring", "backpack",
                         "hostel", "low cost", "save money"),
    TravelStyle.LUXURY: ("luxury", "luxurious", "five star", "5 star", "5-star",
                         "high end", "upscale", "splurge", "business class"),
    TravelStyle.COMFORT: ("comfortable", "comfort", "boutique", "four star", "4 star",
                          "nice hotel", "mid range", "mid-range"),
}

PACE_KEYWORDS: dict[TripPace, tuple[str, ...]] = {
    TripPace.RELAXED: ("relax", "relaxed", "slow", "chill", "unwind", "lazy",
                       "take it easy", "decompress", "rest"),
    TripPace.PACKED: ("packed", "see everything", "action packed", "busy", "intense",
                      "maximise", "as much as possible", "whirlwind"),
}

CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR",
    "₩": "KRW", "₺": "TRY", "R$": "BRL", "CHF": "CHF",
}

CURRENCY_WORDS: dict[str, str] = {
    "dollar": "USD", "dollars": "USD", "usd": "USD", "bucks": "USD",
    "euro": "EUR", "euros": "EUR", "eur": "EUR",
    "pound": "GBP", "pounds": "GBP", "gbp": "GBP", "quid": "GBP",
    "yen": "JPY", "jpy": "JPY", "rupee": "INR", "rupees": "INR", "inr": "INR",
    "franc": "CHF", "chf": "CHF", "won": "KRW", "krw": "KRW",
    "lira": "TRY", "try": "TRY", "real": "BRL", "reais": "BRL", "brl": "BRL",
    "cad": "CAD", "aud": "AUD", "sek": "SEK", "nok": "NOK", "dkk": "DKK",
    "pln": "PLN", "czk": "CZK", "thb": "THB", "sgd": "SGD",
}

MONTHS: dict[str, int] = {
    name.lower(): index
    for index, name in enumerate(calendar.month_name)
    if name
}
MONTHS.update(
    {name.lower(): index for index, name in enumerate(calendar.month_abbr) if name}
)
MONTHS.update({"sept": 9})

# Cities that appear frequently as origins. Used only to disambiguate the
# "from X" pattern -- unknown origins still parse fine, they just are not
# validated here (the geo server validates them later).
_ORIGIN_STOPWORDS = {
    "there", "here", "home", "scratch", "now", "then", "the", "a", "an",
    "my", "our", "your", "this", "that", "somewhere", "anywhere",
}


@lru_cache(maxsize=2048)
def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Compile a whole-word matcher for a keyword or multi-word phrase.

    Plain ``phrase in text`` is wrong here and produces genuinely bad plans:
    "starting 2026-10-12" contains "art", so a date made the traveller an art
    lover; "relaxed pace" contains "relax", so a pace request became a spa
    holiday. Both were observed before this was fixed. Anchoring on word
    boundaries removes the whole class of false positive, while a trailing
    ``\\w*`` still lets a stem match its inflections ("hike" -> "hiking").
    """
    return re.compile(rf"\b{re.escape(phrase)}\w*", re.IGNORECASE)


def _mentions(text: str, keywords: tuple[str, ...]) -> bool:
    """True if any keyword appears in ``text`` as a whole word or phrase."""
    return any(_phrase_pattern(keyword).search(text) for keyword in keywords)


# --------------------------------------------------------------------------
# Field extractors
# --------------------------------------------------------------------------


# "budget" is two different words. As a noun it introduces an amount ("my
# budget is 1800", "budget of about 900 euros"); as an adjective it describes
# how someone travels ("budget trip", "on a budget", "budget-friendly").
# Reading the noun as the adjective made a traveller with a healthy 1800 EUR
# budget get searched for hostels only -- an observed bug. This masks the noun
# usages before style and interest extraction, leaving the adjective ones.
_BUDGET_NOUN = re.compile(
    # The connector group repeats, because people stack them: "budget is
    # about 2000", "budget of around 900", "budget is roughly up to 500".
    # Allowing only one connector let "Budget is about 2000 euros" read as a
    # style signal, which searched hostels for a couple with a 2000 EUR budget.
    r"\bbudget\b(?:\s+(?:of|is|are|at|around|about|under|below|max|maximum|"
    r"roughly|near|approximately|circa|just|up\s+to|:)){0,3}\s*:?\s*(?=[\d$€£¥₹])"
    # "my budget", "our total budget" -- possessives only. "a budget" and
    # "the budget" are deliberately excluded, because "on a budget" is the
    # idiomatic way of saying "I travel cheaply", which IS a style signal.
    r"|\b(?:my|our|total)\s+budget\b(?!\s*(?:trip|travel|holiday|friendly))",
    re.IGNORECASE,
)


def _mask_budget_noun(text: str) -> str:
    """Blank out "budget" where it introduces an amount rather than a style."""
    return _BUDGET_NOUN.sub(" ", text)


def extract_origin(text: str) -> str | None:
    """Find where the traveller is departing from.

    Deliberately conservative: a wrong origin silently ruins every flight
    estimate, so anything ambiguous returns ``None`` and the graph asks.
    """
    patterns = (
        r"\b(?:fly|flying|depart|departing|leave|leaving|travel|travelling|traveling)\s+"
        r"(?:out\s+)?from\s+([A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*){0,2})",
        r"\bfrom\s+([A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*){0,2})\b",
        r"\bi(?:'m| am)\s+(?:based\s+)?in\s+([A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*){0,2})",
        r"\b(?:live|living)\s+in\s+([A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*){0,2})",
        r"\bout\s+of\s+([A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*){0,2})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip(" ,.")
            # The capture is greedy over capitalised words, so "from Paris To
            # Rome" arrives as "Paris To Rome". Cut at the first connector
            # rather than only stripping a trailing one -- the connector marks
            # where the origin ends and the next clause begins.
            candidate = re.split(
                r"\s+(?:to|for|on|in|and|with|around|between|next|early|late|mid|"
                r"during|until|till)\b",
                candidate,
                flags=re.IGNORECASE,
            )[0].strip(" ,.")
            if candidate and candidate.lower() not in _ORIGIN_STOPWORDS and len(candidate) > 2:
                return candidate
    return None


def extract_named_destinations(text: str) -> list[str]:
    """Destinations the user named outright ("take me to Kyoto")."""
    found: list[str] = []
    patterns = (
        r"\b(?:to|visit|visiting|see|explore|go\s+to|head\s+to|thinking\s+(?:about|of))\s+"
        r"([A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*){0,2})",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            candidate = match.group(1).strip(" ,.")
            candidate = re.sub(
                r"\s+(?:From|In|On|For|And|With|Next|Early|Late|Mid)$", "", candidate
            ).strip()
            if (
                candidate
                and len(candidate) > 2
                and candidate.lower() not in _ORIGIN_STOPWORDS
                and candidate not in found
            ):
                found.append(candidate)
    return found[:4]


def extract_dates(text: str, today: date | None = None) -> DateWindow:
    """Parse a date window from natural language.

    Supports explicit ISO ranges, "March 3-10", "next month for 5 days",
    "in 3 weeks", "first week of June" and bare durations. Anything it cannot
    place with confidence is left empty so the graph asks rather than guesses.
    """
    today = today or date.today()
    lowered = text.lower()

    # ---- Duration, useful on its own and to complete a partial range ----
    nights: int | None = None
    # A negative lookbehind for "in" is essential: "in 3 weeks for 4 nights"
    # contains two number-unit pairs, and the leftmost ("3 weeks") is a
    # departure offset, not a trip length. Without this the trip became 21
    # nights long.
    duration = re.search(
        r"(?<!\bin )(?<!\bin  )\b(\d{1,2})\s*[-–]?\s*"
        r"(night|nights|day|days|week|weeks)\b",
        lowered,
    )
    if duration:
        count = int(duration.group(1))
        unit = duration.group(2)
        if unit.startswith("week"):
            nights = count * 7
        elif unit.startswith("day"):
            # "5 days" colloquially means 4 nights, but travellers usually mean
            # 5 nights of accommodation. Treat days as nights; the validator
            # surfaces the assumption.
            nights = count
        else:
            nights = count
    else:
        word_numbers = {
            "a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "fourteen": 14,
        }
        word_duration = re.search(
            r"\b(a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fourteen)"
            r"\s+(night|nights|day|days|week|weeks)\b",
            lowered,
        )
        if word_duration:
            count = word_numbers[word_duration.group(1)]
            nights = count * 7 if word_duration.group(2).startswith("week") else count

    # ---- Explicit ISO range: 2026-05-03 to 2026-05-10 ----
    iso_range = re.search(
        r"(\d{4}-\d{2}-\d{2})\s*(?:to|-|–|until|through|thru)\s*(\d{4}-\d{2}-\d{2})", lowered
    )
    if iso_range:
        try:
            start = date.fromisoformat(iso_range.group(1))
            end = date.fromisoformat(iso_range.group(2))
            return DateWindow(start=start, end=end, flexible=False)
        except ValueError:
            pass

    single_iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", lowered)
    if single_iso:
        try:
            start = date.fromisoformat(single_iso.group(1))
            return DateWindow(
                start=start,
                end=start + timedelta(days=nights or 5),
                flexible=False,
                duration_nights=nights,
            )
        except ValueError:
            pass

    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))

    # ---- "March 3-10" / "3-10 March" / "March 3 to March 10" ----
    day_month_range = re.search(
        rf"\b({month_names})\s+(\d{{1,2}})\s*(?:to|-|–|until|through)\s*"
        rf"(?:({month_names})\s+)?(\d{{1,2}})\b",
        lowered,
    )
    if day_month_range:
        start_month = MONTHS[day_month_range.group(1)]
        start_day = int(day_month_range.group(2))
        end_month = MONTHS.get(day_month_range.group(3) or "", start_month)
        end_day = int(day_month_range.group(4))
        year = today.year if start_month >= today.month else today.year + 1
        try:
            start = date(year, start_month, start_day)
            end_year = year + (1 if end_month < start_month else 0)
            end = date(end_year, end_month, end_day)
            if end > start:
                return DateWindow(start=start, end=end, flexible=False)
        except ValueError:
            pass

    # ---- "first/second week of June", "early June", "mid July", "late August" ----
    part_month = re.search(
        rf"\b(first|second|third|last|early|mid|middle|late)\s+"
        rf"(?:week\s+of\s+|half\s+of\s+)?({month_names})\b",
        lowered,
    )
    if part_month:
        which = part_month.group(1)
        month = MONTHS[part_month.group(2)]
        year = today.year if month >= today.month else today.year + 1
        offsets = {"first": 1, "early": 3, "second": 8, "mid": 14, "middle": 14,
                   "third": 15, "late": 22, "last": 23}
        day = offsets.get(which, 1)
        try:
            start = date(year, month, day)
            return DateWindow(
                start=start,
                end=start + timedelta(days=nights or 6),
                flexible=True,
                duration_nights=nights,
            )
        except ValueError:
            pass

    # ---- Bare month: "in September" ----
    bare_month = re.search(rf"\b(?:in|during|for|around)\s+({month_names})\b", lowered)
    if bare_month:
        month = MONTHS[bare_month.group(1)]
        year = today.year if month >= today.month else today.year + 1
        # Mid-month start is the least-wrong default for an unanchored month.
        start = date(year, month, 12)
        if start < today:
            start = today + timedelta(days=21)
        return DateWindow(
            start=start,
            end=start + timedelta(days=nights or 6),
            flexible=True,
            duration_nights=nights,
        )

    # ---- Relative: "next month", "in 3 weeks", "next weekend" ----
    if re.search(r"\bnext\s+month\b", lowered):
        first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=10)
        return DateWindow(
            start=first_next,
            end=first_next + timedelta(days=nights or 6),
            flexible=True,
            duration_nights=nights,
        )

    relative = re.search(r"\bin\s+(\d{1,2})\s+(day|days|week|weeks|month|months)\b", lowered)
    if relative:
        count = int(relative.group(1))
        unit = relative.group(2)
        delta = (
            timedelta(days=count) if unit.startswith("day")
            else timedelta(weeks=count) if unit.startswith("week")
            else timedelta(days=30 * count)
        )
        start = today + delta
        return DateWindow(
            start=start,
            end=start + timedelta(days=nights or 6),
            flexible=True,
            duration_nights=nights,
        )

    if re.search(r"\bnext\s+week(?:end)?\b", lowered):
        days_ahead = (4 - today.weekday()) % 7 + 7  # next Friday
        start = today + timedelta(days=days_ahead)
        weekend = "weekend" in lowered
        return DateWindow(
            start=start,
            end=start + timedelta(days=nights or (2 if weekend else 6)),
            flexible=True,
            duration_nights=nights,
        )

    if re.search(r"\b(this|next)\s+(summer|winter|spring|autumn|fall)\b", lowered):
        season_start = {"spring": 4, "summer": 7, "autumn": 10, "fall": 10, "winter": 1}
        season = re.search(r"\b(summer|winter|spring|autumn|fall)\b", lowered).group(1)
        month = season_start[season]
        year = today.year if month >= today.month else today.year + 1
        start = date(year, month, 10)
        return DateWindow(
            start=start,
            end=start + timedelta(days=nights or 6),
            flexible=True,
            duration_nights=nights,
        )

    # Duration only ("a week somewhere warm") -- flexible window, no anchor.
    return DateWindow(duration_nights=nights, flexible=True)


def extract_budget(text: str) -> Budget:
    """Parse a stated budget, its currency, and whether it is per person."""
    lowered = text.lower()

    # Symbol-prefixed: $2,500 / €1.2k / £800
    symbol_match = re.search(
        r"([$€£¥₹₩₺]|r\$|chf)\s?(\d[\d,.\s]*)\s*(k|thousand)?", lowered
    )
    amount: float | None = None
    currency = "EUR"

    if symbol_match:
        symbol = symbol_match.group(1)
        raw = symbol_match.group(2).replace(",", "").replace(" ", "").rstrip(".")
        try:
            amount = float(raw)
            if symbol_match.group(3):
                amount *= 1000
            currency = CURRENCY_SYMBOLS.get(
                symbol.upper() if symbol.lower() in ("r$", "chf") else symbol, "USD"
            )
        except ValueError:
            amount = None

    if amount is None:
        # Word-suffixed: "2500 euros", "1.5k usd", "budget of 900 pounds"
        word_match = re.search(
            r"(\d[\d,.\s]*)\s*(k|thousand)?\s*"
            r"(dollars?|usd|bucks|euros?|eur|pounds?|gbp|quid|yen|jpy|rupees?|inr|"
            r"francs?|chf|won|krw|lira|try|reais|real|brl|cad|aud|sek|nok|dkk|pln|czk|thb|sgd)\b",
            lowered,
        )
        if word_match:
            raw = word_match.group(1).replace(",", "").replace(" ", "").rstrip(".")
            try:
                amount = float(raw)
                if word_match.group(2):
                    amount *= 1000
                currency = CURRENCY_WORDS.get(word_match.group(3), "EUR")
            except ValueError:
                amount = None

    if amount is None:
        # Bare number near the word "budget".
        near_budget = re.search(
            r"budget(?:\s+(?:of|is|around|about|under|max|maximum))?\s*"
            r"(?:of\s+)?(\d[\d,.\s]*)\s*(k|thousand)?",
            lowered,
        )
        if near_budget:
            raw = near_budget.group(1).replace(",", "").replace(" ", "").rstrip(".")
            try:
                amount = float(raw)
                if near_budget.group(2):
                    amount *= 1000
            except ValueError:
                amount = None

    # Per-person vs total. Getting this backwards doubles or halves the budget
    # and silently ruins every affordability check, so an explicit "total"
    # phrasing always wins over the per-person default. The group phrasings
    # matter most: "1800 for two of us" is unambiguously a total, but reads as
    # per-person to a naive rule -- which is exactly the bug this catches.
    explicit_per_person = re.search(
        r"\b(per person|per head|each|pp|a head|apiece)\b", lowered
    )
    explicit_total = re.search(
        r"\b(total|in total|combined|all[- ]in|altogether|between us|"
        r"for (?:the )?(?:both|two|three|four|five|all) of us|"
        r"for (?:us|the group|the family|everyone|all)|for \d+ (?:of us|people))\b",
        lowered,
    )
    if explicit_total and not explicit_per_person:
        per_person = False
    elif explicit_per_person:
        per_person = True
    else:
        # Unqualified: a lone traveller's budget is trivially both, and for a
        # group people far more often quote the whole pot than a per-head share.
        per_person = extract_party(text).total == 1

    is_hard = not re.search(r"\b(flexible|roughly|around|about|ish|or so|give or take)\b", lowered)

    return Budget(
        amount=amount,
        currency=currency,
        per_person=per_person,
        is_hard_limit=is_hard,
        amount_eur=amount if currency == "EUR" else None,
    )


def extract_party(text: str) -> PartyComposition:
    """Parse how many people are travelling, and whether any are children."""
    lowered = text.lower()
    adults, children, infants = 1, 0, 0

    adult_match = re.search(r"\b(\d{1,2})\s+adults?\b", lowered)
    if adult_match:
        adults = min(12, max(1, int(adult_match.group(1))))
    else:
        word_numbers = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                        "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        people = re.search(
            r"\b(\d{1,2}|two|three|four|five|six|seven|eight|nine|ten)\s+"
            r"(?:of us|people|persons|travellers|travelers|friends|colleagues)\b",
            lowered,
        )
        if people:
            token = people.group(1)
            adults = word_numbers.get(token) or min(12, max(1, int(token)))
        elif re.search(r"\b(my (wife|husband|partner|girlfriend|boyfriend)|"
                       r"we\b|us\b|couple|the two of us|both of us)", lowered):
            adults = 2

    child_match = re.search(r"\b(\d{1,2})\s+(?:kids?|children|child)\b", lowered)
    if child_match:
        children = min(10, int(child_match.group(1)))
    elif re.search(r"\b(with (?:my |our )?(?:kids|children)|family trip|"
                   r"child[- ]friendly|toddler)", lowered):
        children = 2  # Stated but uncounted; the clarifier confirms.

    infant_match = re.search(r"\b(\d{1,2})\s+(?:infants?|babies|baby)\b", lowered)
    if infant_match:
        infants = min(6, int(infant_match.group(1)))

    # "a family of four" means four people in total, not four adults.
    family_of = re.search(r"\bfamily of (\d{1,2}|two|three|four|five|six)\b", lowered)
    if family_of:
        word_numbers = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
        token = family_of.group(1)
        total = word_numbers.get(token) or int(token)
        adults = min(2, total)
        children = max(0, total - adults)

    return PartyComposition(adults=adults, children=children, infants=infants)


def extract_interests(text: str) -> list[str]:
    """Map free text onto the canonical interest vocabulary."""
    lowered = _mask_budget_noun(text.lower())
    found: list[str] = []
    for interest, keywords in INTEREST_KEYWORDS.items():
        if _mentions(lowered, keywords):
            found.append(interest)
    return found


def extract_regions(text: str) -> list[str]:
    """Extract a geographic restriction such as "somewhere in Europe".

    Without this, "a beach trip in Europe" happily returned Marrakesh and
    Buenos Aires -- the region words were simply ignored. The values returned
    are the UN sub-region names the destination catalogue is keyed by, so a
    broad phrase like "Europe" expands to every European sub-region.
    """
    lowered = text.lower()
    regions: list[str] = []

    groups: dict[str, tuple[str, ...]] = {
        "europe": ("Northern Europe", "Western Europe", "Southern Europe",
                   "Central Europe", "Eastern Europe"),
        "asia": ("Eastern Asia", "South-eastern Asia", "Southern Asia",
                 "Western Asia", "Central Asia"),
        "africa": ("Northern Africa", "Sub-Saharan Africa", "Southern Africa"),
        "south america": ("South America",),
        "latin america": ("South America", "Central America"),
        "central america": ("Central America",),
        "north america": ("Northern America",),
        "the americas": ("Northern America", "Central America", "South America"),
        "oceania": ("Australia and New Zealand", "Melanesia", "Polynesia"),
        "caribbean": ("Caribbean",),
        "scandinavia": ("Northern Europe",),
        "nordics": ("Northern Europe",),
        "the balkans": ("Southern Europe",),
        "mediterranean": ("Southern Europe",),
        "middle east": ("Western Asia",),
        "southeast asia": ("South-eastern Asia",),
        "south east asia": ("South-eastern Asia",),
        "east asia": ("Eastern Asia",),
        "far east": ("Eastern Asia", "South-eastern Asia"),
    }

    # Longest phrases first, so "southeast asia" is not shadowed by "asia".
    for phrase in sorted(groups, key=len, reverse=True):
        # Require a locational preposition so "Asian food" is not a region.
        if re.search(
            rf"\b(?:in|to|within|around|across|somewhere in|anywhere in)\s+"
            rf"(?:the\s+)?{re.escape(phrase)}\b",
            lowered,
        ):
            for region in groups[phrase]:
                if region not in regions:
                    regions.append(region)
            break

    return regions


def extract_climate(text: str) -> ClimatePreference:
    lowered = text.lower()
    # Check the most specific preferences first: "skiing" implies snow, and
    # snow should win over a generic "cool".
    for preference in (ClimatePreference.SNOW, ClimatePreference.DRY,
                       ClimatePreference.WARM, ClimatePreference.COOL,
                       ClimatePreference.MILD):
        if _mentions(lowered, CLIMATE_KEYWORDS[preference]):
            return preference
    return ClimatePreference.ANY


def extract_style(text: str) -> TravelStyle:
    lowered = _mask_budget_noun(text.lower())
    for style in (TravelStyle.LUXURY, TravelStyle.BUDGET, TravelStyle.COMFORT):
        if _mentions(lowered, STYLE_KEYWORDS[style]):
            return style
    return TravelStyle.BALANCED


def extract_pace(text: str) -> TripPace:
    lowered = text.lower()
    for pace in (TripPace.PACKED, TripPace.RELAXED):
        if _mentions(lowered, PACE_KEYWORDS[pace]):
            return pace
    return TripPace.BALANCED


def extract_constraints(text: str) -> Constraints:
    """Pick up hard requirements: accessibility, diet, flight limits, exclusions."""
    lowered = text.lower()
    constraints = Constraints()

    if re.search(r"\b(wheelchair|accessible|accessibility|step[- ]free|mobility|"
                 r"disabled|limited mobility)\b", lowered):
        constraints.accessibility_required = True

    for diet, keywords in {
        "vegetarian": ("vegetarian",),
        "vegan": ("vegan",),
        "halal": ("halal",),
        "kosher": ("kosher",),
        "gluten_free": ("gluten free", "gluten-free", "coeliac", "celiac"),
        "nut_allergy": ("nut allergy", "peanut allergy", "allergic to nuts"),
    }.items():
        if _mentions(lowered, keywords):
            constraints.dietary.append(diet)

    for note, keywords in {
        "asthma": ("asthma", "asthmatic"),
        "pollen_allergy": ("hay fever", "pollen allergy", "allergic to pollen"),
        "altitude_sensitive": ("altitude sickness", "altitude sensitive"),
        "reduced_mobility": ("bad knee", "bad back", "cannot walk far", "can't walk far"),
    }.items():
        if _mentions(lowered, keywords):
            constraints.health_notes.append(note)

    flight_limit = re.search(
        r"\b(?:no more than|under|less than|max(?:imum)?|within)\s+"
        r"(\d{1,2})\s*(?:h|hr|hrs|hour|hours)\s*(?:flight|flying|in the air)?",
        lowered,
    )
    if flight_limit:
        constraints.max_flight_hours = float(flight_limit.group(1))
    elif re.search(r"\b(short flight|short haul|nothing too far|nearby)\b", lowered):
        constraints.max_flight_hours = 4.0

    if re.search(r"\b(direct|non[- ]?stop|no layover|no connection|no stops)\b", lowered):
        constraints.max_stops = 0
    elif re.search(r"\b(one stop|1 stop|at most one connection)\b", lowered):
        constraints.max_stops = 1

    for match in re.finditer(
        r"\b(?:avoid|not|no|except|excluding|anywhere but|don'?t want)\s+"
        r"([A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*){0,2})",
        text,
    ):
        candidate = match.group(1).strip(" ,.")
        if candidate and len(candidate) > 2 and candidate.lower() not in _ORIGIN_STOPWORDS:
            constraints.avoid_cities.append(candidate)

    if re.search(r"\bvisa[- ]free\b|\bno visa\b|\bwithout a visa\b", lowered):
        constraints.visa_free_only = True

    return constraints


def extract_purpose(text: str) -> str | None:
    lowered = text.lower()
    for purpose, keywords in {
        "honeymoon": ("honeymoon",),
        "anniversary": ("anniversary",),
        "birthday": ("birthday",),
        "family holiday": ("family trip", "family holiday", "family vacation"),
        "solo travel": ("solo", "by myself", "on my own", "alone"),
        "business": ("business trip", "conference", "work trip"),
        "friends trip": ("with friends", "friends trip", "group trip", "bachelor",
                         "bachelorette", "stag", "hen do"),
        "workation": ("workation", "remote work", "digital nomad"),
    }.items():
        if _mentions(lowered, keywords):
            return purpose
    return None


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def build_brief(text: str, today: date | None = None) -> TravelBrief:
    """Parse a full travel request into a :class:`TravelBrief`, no LLM needed."""
    text = (text or "").strip()
    dates = extract_dates(text, today=today)
    interests = extract_interests(text)
    climate = extract_climate(text)

    # A stated interest can imply a climate the user did not name.
    if climate == ClimatePreference.ANY:
        if "beach" in interests or "surfing" in interests or "diving" in interests:
            climate = ClimatePreference.WARM
        elif "skiing" in interests:
            climate = ClimatePreference.SNOW

    brief = TravelBrief(
        raw_query=text,
        origin=extract_origin(text),
        named_destinations=extract_named_destinations(text),
        regions=extract_regions(text),
        dates=dates,
        party=extract_party(text),
        budget=extract_budget(text),
        interests=interests,
        climate_preference=climate,
        pace=extract_pace(text),
        style=extract_style(text),
        constraints=extract_constraints(text),
        trip_purpose=extract_purpose(text),
        extraction_method="rules",
    )

    brief.missing_critical = compute_missing(brief)
    # Confidence rises with how much we actually pinned down.
    filled = sum(
        [
            bool(brief.origin),
            brief.dates.is_complete,
            bool(brief.interests),
            brief.budget.amount is not None,
        ]
    )
    brief.confidence = round(0.28 + 0.16 * filled, 2)
    return brief


def compute_missing(brief: TravelBrief) -> list[str]:
    """Which critical fields still block responsible planning.

    Only two things truly block: we cannot price flights without an origin, and
    we cannot score weather or fares without dates. Budget and interests have
    workable defaults, so they are asked about but never block.
    """
    missing: list[str] = []
    if not brief.origin:
        missing.append("origin")
    if not brief.dates.is_complete and not brief.dates.duration_nights:
        missing.append("dates")
    return missing


def merge_briefs(rules: TravelBrief, llm_brief: TravelBrief) -> TravelBrief:
    """Overlay LLM output on rule-based output, keeping the best of each.

    The LLM wins on interpretation (interests, purpose, climate nuance); the
    rules win on anything they extracted verbatim and the LLM left empty. This
    is why a model that forgets to echo the budget cannot lose it.
    """
    merged = llm_brief.model_copy(deep=True)
    merged.raw_query = rules.raw_query
    merged.extraction_method = "merged"

    if not merged.origin:
        merged.origin = rules.origin
    if not merged.named_destinations:
        merged.named_destinations = rules.named_destinations
    if not merged.dates.is_complete and rules.dates.is_complete:
        merged.dates = rules.dates
    elif merged.dates.duration_nights is None and rules.dates.duration_nights:
        merged.dates.duration_nights = rules.dates.duration_nights

    if merged.budget.amount is None and rules.budget.amount is not None:
        merged.budget = rules.budget
    if not merged.interests:
        merged.interests = rules.interests
    else:
        # Union, preserving the LLM's ordering (it ranks by emphasis).
        merged.interests = list(
            dict.fromkeys(list(merged.interests) + list(rules.interests))
        )

    if merged.climate_preference == ClimatePreference.ANY.value:
        merged.climate_preference = rules.climate_preference
    if not merged.trip_purpose:
        merged.trip_purpose = rules.trip_purpose

    # Constraints are safety-relevant: union them, never drop one.
    merged.constraints.accessibility_required = (
        merged.constraints.accessibility_required or rules.constraints.accessibility_required
    )
    merged.constraints.visa_free_only = (
        merged.constraints.visa_free_only or rules.constraints.visa_free_only
    )
    merged.constraints.dietary = list(
        dict.fromkeys(merged.constraints.dietary + rules.constraints.dietary)
    )
    merged.constraints.health_notes = list(
        dict.fromkeys(merged.constraints.health_notes + rules.constraints.health_notes)
    )
    merged.constraints.avoid_cities = list(
        dict.fromkeys(merged.constraints.avoid_cities + rules.constraints.avoid_cities)
    )
    if merged.constraints.max_flight_hours is None:
        merged.constraints.max_flight_hours = rules.constraints.max_flight_hours
    if merged.constraints.max_stops is None:
        merged.constraints.max_stops = rules.constraints.max_stops

    if rules.party.total > 1 and merged.party.total == 1:
        merged.party = rules.party

    merged.missing_critical = compute_missing(merged)
    merged.confidence = max(merged.confidence, rules.confidence)
    return merged
