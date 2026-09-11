"""Selection node -- the second human-in-the-loop gate.

The graph presents its shortlist and stops. A travel assistant that picks the
destination for you is not an assistant; the whole point of the recommendation
stage is to hand the decision back.

Resolving the user's reply is deliberately forgiving, because people answer
this question in every possible way: "Lisbon", "the second one", "number 3",
"let's do Portugal", "the cheap one", or just "yes". The resolver tries exact
match, ordinal, country, then fuzzy substring, and only then asks again.
"""

from __future__ import annotations

import logging
import re

from langgraph.types import interrupt

from ...observability import STAGE_ENTERED, langfuse_hook, span, time_agent
from ...schemas.travel import DestinationCandidate
from ..state import AgentState

logger = logging.getLogger(__name__)

_ORDINAL_WORDS: dict[str, int] = {
    "first": 0, "1st": 0, "one": 0, "top": 0, "1": 0,
    "second": 1, "2nd": 1, "two": 1, "2": 1,
    "third": 2, "3rd": 2, "three": 2, "3": 2,
    "fourth": 3, "4th": 3, "four": 3, "4": 3,
    "fifth": 4, "5th": 4, "five": 4, "5": 4,
    "sixth": 5, "6th": 5, "six": 5, "6": 5,
    "last": -1,
}

_AFFIRMATIVE = {
    "yes", "yeah", "yep", "sure", "ok", "okay", "sounds good", "go ahead",
    "do it", "that one", "perfect", "great", "lets do it", "let's do it",
    "book it", "plan it", "the first", "your pick", "you choose", "whatever",
}


def resolve_selection(
    answer: str, candidates: list[DestinationCandidate]
) -> tuple[DestinationCandidate | None, str]:
    """Work out which candidate the user meant.

    Returns ``(candidate, how)`` where ``how`` explains the match, which is
    surfaced in the trace so a wrong interpretation is debuggable rather than
    mysterious.
    """
    if not candidates:
        return None, "no_candidates"

    text = (answer or "").strip().lower()
    if not text:
        return None, "empty"

    stripped = re.sub(r"[^a-z0-9\s']", " ", text)
    stripped = re.sub(r"\s+", " ", stripped).strip()

    # 1. Exact or contained city name -- the most common and most reliable.
    for index, candidate in enumerate(candidates):
        city = candidate.city.lower()
        if city == stripped or re.search(rf"\b{re.escape(city)}\b", stripped):
            return candidate, f"city_name:{index}"

    # 2. Country name ("let's do Portugal").
    for index, candidate in enumerate(candidates):
        country = (candidate.country or "").lower()
        if country and re.search(rf"\b{re.escape(country)}\b", stripped):
            return candidate, f"country_name:{index}"

    # 3. Ordinal reference, but only when it reads like a choice. A bare
    #    number in "4 nights please" must not select candidate four.
    ordinal_context = re.search(
        r"\b(the\s+)?(first|second|third|fourth|fifth|sixth|last|1st|2nd|3rd|4th|5th|6th)\b"
        r"|\b(number|option|choice|pick)\s*(\d)\b",
        stripped,
    )
    if ordinal_context:
        token = ordinal_context.group(2) or ordinal_context.group(4)
        if token in _ORDINAL_WORDS:
            index = _ORDINAL_WORDS[token]
            index = len(candidates) - 1 if index == -1 else index
            if 0 <= index < len(candidates):
                return candidates[index], f"ordinal:{index}"

    # 4. Superlative reference to a scored attribute.
    if re.search(r"\b(cheap|cheapest|budget|least expensive)\b", stripped):
        priced = [c for c in candidates if c.estimated_trip_total_eur is not None]
        if priced:
            pick = min(priced, key=lambda c: c.estimated_trip_total_eur or 0)
            return pick, "cheapest"
    if re.search(r"\b(warm|warmest|sunniest|best weather|nicest weather)\b", stripped):
        rated = [c for c in candidates if c.weather.score is not None]
        if rated:
            pick = max(rated, key=lambda c: c.weather.score or 0)
            return pick, "best_weather"

    # 5. A bare affirmative means "your top pick".
    if stripped in _AFFIRMATIVE or any(
        stripped.startswith(phrase) for phrase in _AFFIRMATIVE
    ):
        return candidates[0], "affirmative_top_pick"

    # 6. Fuzzy: a distinctive prefix of a city name ("barce").
    for index, candidate in enumerate(candidates):
        city = candidate.city.lower()
        if len(stripped) >= 4 and city.startswith(stripped[:4]):
            return candidate, f"prefix:{index}"

    return None, "unresolved"


async def selection_node(state: AgentState) -> AgentState:
    """Present the shortlist, then wait for and interpret the user's choice."""
    with time_agent("selection"):
        STAGE_ENTERED.labels(stage="awaiting_choice").inc()

        recommendation = state.get("recommendation")
        candidates = list(state.get("candidates") or [])

        if recommendation is None or not candidates:
            return AgentState(stage="error", errors=["No recommendation to select from."])

        options = [
            {
                "index": i,
                "city": c.city,
                "country": c.country,
                "score": c.composite_score,
                "weather_score": c.weather.score,
                "estimated_total_eur": c.estimated_trip_total_eur,
                "why": c.why[:2],
            }
            for i, c in enumerate(candidates)
        ]

        with span("agent.selection", **{"selection.options": len(options)}) as current:
            # Suspend until the user answers. The payload is what the UI
            # renders as selectable cards.
            answer = interrupt(
                {
                    "type": "destination_choice",
                    "question": recommendation.follow_up_question,
                    "headline": recommendation.headline,
                    "rationale": recommendation.rationale,
                    "options": options,
                }
            )

            # The API can pass a structured choice (a card click) or free text.
            selected: DestinationCandidate | None = None
            how = "unresolved"

            if isinstance(answer, dict):
                index = answer.get("selected_index")
                if isinstance(index, int) and 0 <= index < len(candidates):
                    selected, how = candidates[index], f"explicit_index:{index}"
                else:
                    selected, how = resolve_selection(
                        str(answer.get("value") or ""), candidates
                    )
                raw_answer = str(answer.get("value") or "")
            else:
                raw_answer = str(answer or "")
                selected, how = resolve_selection(raw_answer, candidates)

            if selected is None:
                # One re-ask, then default to the top pick and say so. Looping
                # on an ambiguous answer is more annoying than a stated
                # assumption the user can correct.
                retry = interrupt(
                    {
                        "type": "destination_choice_retry",
                        "question": (
                            "I didn't catch which one you meant. You can say the "
                            "city name, or 'the first one'."
                        ),
                        "options": options,
                    }
                )
                retry_text = (
                    str(retry.get("value")) if isinstance(retry, dict) else str(retry or "")
                )
                if isinstance(retry, dict) and isinstance(retry.get("selected_index"), int):
                    index = retry["selected_index"]
                    if 0 <= index < len(candidates):
                        selected, how = candidates[index], f"explicit_index:{index}"
                if selected is None:
                    selected, how = resolve_selection(retry_text, candidates)
                if selected is None:
                    selected, how = candidates[0], "defaulted_to_top"
                raw_answer = retry_text

            current.set_attribute("selection.city", selected.city)
            current.set_attribute("selection.method", how)

        logger.info("Selected %s via %s", selected.city, how)

        langfuse_hook.log_span(
            state.get("langfuse_trace"),
            name="selection",
            input_data={"answer": raw_answer},
            output_data={"city": selected.city, "method": how},
        )

        note = ""
        if how == "defaulted_to_top":
            note = (
                f"I wasn't sure which you meant, so I've gone with my top pick, "
                f"{selected.label}. Say another city name if you'd rather change."
            )

        return AgentState(
            selected_destination=selected,
            selection_raw=raw_answer,
            stage="planning",
            awaiting_user=False,
            messages=[{"role": "user", "content": raw_answer}] if raw_answer else [],
            scratch={"selection_method": how, "selection_note": note},
        )
