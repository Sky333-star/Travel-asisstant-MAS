"""Composite destination scoring.

The Recommender needs one number per candidate, but it must be a number a
human can argue with. So scoring here is a transparent weighted sum of four
independent signals, each already normalised to 0-100:

* **Interest fit** -- how much of what the traveller asked for this place has.
* **Weather fit** -- from real forecast or climate data, via the weather MCP.
* **Season fit** -- whether these dates are the place's good months.
* **Affordability** -- estimated trip cost against the stated budget.

Weights shift with what the traveller emphasised. Someone who named a climate
preference gets weather weighted harder; someone with a hard budget gets cost
weighted harder. The weights used are attached to every candidate so the UI
can show *why* the ranking came out the way it did -- an opaque score is
useless for a decision the user has to own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas.travel import ClimatePreference, DestinationCandidate, TravelBrief


@dataclass
class ScoreWeights:
    """Relative importance of each signal. Normalised before use."""

    interest: float = 0.34
    weather: float = 0.30
    season: float = 0.16
    affordability: float = 0.20
    rationale: list[str] = field(default_factory=list)

    def normalised(self) -> ScoreWeights:
        total = self.interest + self.weather + self.season + self.affordability
        if total <= 0:
            return ScoreWeights()
        return ScoreWeights(
            interest=self.interest / total,
            weather=self.weather / total,
            season=self.season / total,
            affordability=self.affordability / total,
            rationale=list(self.rationale),
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "interest": round(self.interest, 3),
            "weather": round(self.weather, 3),
            "season": round(self.season, 3),
            "affordability": round(self.affordability, 3),
        }


def derive_weights(brief: TravelBrief) -> ScoreWeights:
    """Choose scoring weights from what the traveller actually emphasised."""
    weights = ScoreWeights()
    notes: list[str] = []

    climate = brief.climate_preference
    climate_value = climate.value if isinstance(climate, ClimatePreference) else climate

    if climate_value and climate_value != ClimatePreference.ANY.value:
        # A named climate preference is a strong, explicit signal.
        weights.weather += 0.12
        weights.interest -= 0.06
        weights.affordability -= 0.06
        notes.append(f"Weather weighted higher: you asked for {climate_value} conditions.")

    if brief.budget.amount is not None and brief.budget.is_hard_limit:
        weights.affordability += 0.12
        weights.interest -= 0.06
        weights.season -= 0.06
        notes.append("Cost weighted higher: you set a firm budget.")
    elif brief.budget.amount is None:
        # With no budget stated, cost is a weak signal -- redistribute it.
        weights.affordability -= 0.10
        weights.interest += 0.06
        weights.weather += 0.04
        notes.append("Cost weighted lower: no budget given, so fit leads.")

    if len(brief.interests) >= 4:
        weights.interest += 0.08
        weights.season -= 0.04
        weights.affordability -= 0.04
        notes.append("Interest fit weighted higher: you named several specific interests.")

    if brief.dates.flexible:
        # Flexible dates make season fit less decisive -- we could shift.
        weights.season -= 0.06
        weights.weather += 0.06
        notes.append("Season weighted lower: your dates are flexible.")

    # Keep every weight non-negative before normalising.
    weights.interest = max(0.05, weights.interest)
    weights.weather = max(0.05, weights.weather)
    weights.season = max(0.02, weights.season)
    weights.affordability = max(0.02, weights.affordability)
    weights.rationale = notes
    return weights.normalised()


def affordability_score(
    estimated_total_eur: float | None,
    budget_eur: float | None,
    budget_tier: int | None,
) -> float | None:
    """Score cost against budget, or fall back to the coarse price tier.

    Scoring is asymmetric on purpose. Coming in under budget is good but
    saturates -- half price is not twice as good. Going over is punished
    steeply, because a trip the traveller cannot pay for is not a trip.
    """
    if estimated_total_eur is not None and budget_eur:
        ratio = estimated_total_eur / budget_eur
        if ratio <= 0.6:
            return 100.0
        if ratio <= 1.0:
            # 0.6 -> 100, 1.0 -> 72: comfortably affordable stays strong.
            return round(100.0 - (ratio - 0.6) * 70.0, 1)
        if ratio <= 1.5:
            # 1.0 -> 72 down to 1.5 -> 12: over budget degrades fast.
            return round(max(0.0, 72.0 - (ratio - 1.0) * 120.0), 1)
        return 0.0

    if budget_tier is not None:
        # Without a budget, cheaper is mildly preferable, all else equal.
        return {1: 92.0, 2: 78.0, 3: 60.0, 4: 42.0}.get(budget_tier, 65.0)

    return None


def score_candidate(
    candidate: DestinationCandidate,
    weights: ScoreWeights,
    budget_eur: float | None,
) -> DestinationCandidate:
    """Compute a candidate's composite score and the reasons behind it.

    Missing signals are handled by renormalising over the signals we *do*
    have, rather than substituting a neutral 50. Treating "no weather data" as
    an average score would quietly rank an unknown destination above a known
    mediocre one.
    """
    signals: list[tuple[str, float, float]] = []

    if candidate.interest_score is not None:
        signals.append(("interest", candidate.interest_score, weights.interest))
    if candidate.weather.score is not None:
        signals.append(("weather", candidate.weather.score, weights.weather))
    if candidate.season_score is not None:
        signals.append(("season", candidate.season_score, weights.season))

    affordability = affordability_score(
        candidate.estimated_trip_total_eur, budget_eur, candidate.budget_tier
    )
    candidate.affordability_score = affordability
    if affordability is not None:
        signals.append(("affordability", affordability, weights.affordability))

    if not signals:
        candidate.composite_score = None
        return candidate

    weight_total = sum(w for _, _, w in signals) or 1.0
    composite = sum(value * weight for _, value, weight in signals) / weight_total

    # Confidence discount: a score built from climate normals is weaker
    # evidence than one built from a live forecast, and the ranking should say
    # so rather than pretending to equal certainty.
    if candidate.weather.basis == "climate_normals":
        composite *= 0.97
    elif candidate.weather.basis == "unavailable":
        composite *= 0.93

    candidate.composite_score = round(max(0.0, min(100.0, composite)), 1)
    candidate.why = build_reasons(candidate, budget_eur)
    return candidate


def build_reasons(candidate: DestinationCandidate, budget_eur: float | None) -> list[str]:
    """Human-readable justification for a candidate's rank."""
    reasons: list[str] = []

    if candidate.matched_interests:
        matched = ", ".join(candidate.matched_interests[:4])
        reasons.append(f"Matches your interest in {matched}.")

    weather = candidate.weather
    if weather.score is not None:
        qualifier = (
            "Excellent" if weather.score >= 80 else
            "Good" if weather.score >= 62 else
            "Mixed" if weather.score >= 42 else
            "Poor"
        )
        detail = ""
        if weather.avg_high_c is not None:
            detail = f", highs around {weather.avg_high_c:.0f}C"
        if weather.rainy_days is not None and weather.rainy_days >= 1:
            detail += f" with {weather.rainy_days:.0f} rainy days"
        basis = (
            " (historical averages, not a forecast this far out)"
            if weather.basis == "climate_normals" else ""
        )
        reasons.append(f"{qualifier} weather for your dates{detail}{basis}.")

    if candidate.season_score is not None and candidate.season_score >= 90:
        reasons.append("Your dates fall in its best travel season.")
    elif candidate.season_score is not None and candidate.season_score <= 30:
        reasons.append("Your dates fall outside its ideal season.")

    if candidate.estimated_trip_total_eur is not None:
        total = candidate.estimated_trip_total_eur
        if budget_eur:
            delta = total - budget_eur
            if delta <= 0:
                reasons.append(
                    f"Estimated at about EUR {total:,.0f}, "
                    f"roughly EUR {abs(delta):,.0f} under your budget."
                )
            else:
                reasons.append(
                    f"Estimated at about EUR {total:,.0f}, "
                    f"about EUR {delta:,.0f} over your budget."
                )
        else:
            reasons.append(f"Estimated trip cost around EUR {total:,.0f}.")

    if weather.air_quality_band and weather.air_quality_band in ("poor", "very poor",
                                                                 "extremely poor"):
        reasons.append(f"Air quality is currently {weather.air_quality_band}.")

    return reasons


def rank_candidates(
    candidates: list[DestinationCandidate],
    brief: TravelBrief,
    budget_eur: float | None,
) -> tuple[list[DestinationCandidate], ScoreWeights]:
    """Score and sort candidates best-first."""
    weights = derive_weights(brief)
    scored = [score_candidate(candidate, weights, budget_eur) for candidate in candidates]
    # Unscoreable candidates sink to the bottom; ties break on city name so
    # the ordering is stable across runs.
    scored.sort(key=lambda c: (-(c.composite_score or -1.0), c.city))
    return scored, weights


def interest_overlap_score(
    candidate_tags: list[str], wanted: list[str]
) -> tuple[float, list[str]]:
    """Fraction of the traveller's interests this destination covers."""
    if not wanted:
        return 55.0, []  # Neutral-positive: nothing asked, nothing failed.
    tags = {t.lower() for t in candidate_tags}
    matched = [w for w in wanted if w.lower() in tags]
    return round(100.0 * len(matched) / len(wanted), 1), matched


def season_fit_score(best_months: list[int], travel_month: int | None) -> float | None:
    """How well the travel month matches a destination's best months."""
    if travel_month is None or not best_months:
        return None
    if travel_month in best_months:
        return 100.0
    # Adjacent months are a near miss; wrap-around at December/January.
    if any(abs(((travel_month - m + 6) % 12) - 6) <= 1 for m in best_months):
        return 58.0
    return 20.0
