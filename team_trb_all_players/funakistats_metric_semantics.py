#!/usr/bin/env python3
"""Conservative metric-direction semantics for @funakistats impact graphics.

The purpose of this module is to prevent a numerically positive ON-minus-OFF swing from
being described as universally "better" when the basketball meaning of the metric may
run in the opposite direction. Unknown metrics deliberately remain direction-neutral.

Directions refer to the underlying team metric, not to the sign of ON-minus-OFF:
  +1: higher underlying value is generally favorable
  -1: lower underlying value is generally favorable
   0: context-dependent / descriptive / not safe to label favorable without review

This layer is editorial semantics only. It never changes source values, coordinates,
rank calculations, or corrected-OFF methodology.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSemantics:
    direction: int
    label: str
    rationale: str


# Keep this registry intentionally small and high-confidence. Add metrics only when the
# direction is stable enough that "favorable" language is defensible without context.
METRIC_SEMANTICS: dict[str, MetricSemantics] = {
    "TotalReboundPct": MetricSemantics(+1, "TREB%", "A higher team share of available rebounds is favorable."),
    "OffReboundPct": MetricSemantics(+1, "OREB%", "A higher team share of available offensive rebounds is favorable."),
    "DefReboundPct": MetricSemantics(+1, "DREB%", "A higher team share of available defensive rebounds is favorable."),
    "OffRating": MetricSemantics(+1, "OFF RTG", "More points scored per possession is favorable."),
    "DefRating": MetricSemantics(-1, "DEF RTG", "Fewer points allowed per possession is favorable."),
    "NetRating": MetricSemantics(+1, "NET RTG", "A higher scoring margin per possession is favorable."),
    "EffectiveFgPct": MetricSemantics(+1, "eFG%", "Higher effective field-goal percentage is favorable."),
    "TsPct": MetricSemantics(+1, "TS%", "Higher true-shooting percentage is favorable."),
    "TurnoverPct": MetricSemantics(-1, "TOV%", "A lower offensive turnover rate is generally favorable."),
    "AssistPct": MetricSemantics(+1, "AST%", "Higher team assist rate is treated as favorable for this presentation."),
    # Pace is deliberately neutral: faster or slower is not intrinsically better.
    "Pace": MetricSemantics(0, "PACE", "Pace is stylistic/contextual rather than intrinsically favorable in either direction."),
}


def semantics_for(metric: str) -> MetricSemantics:
    """Return known semantics; unknown metrics are explicitly direction-neutral."""
    key = str(metric)
    return METRIC_SEMANTICS.get(
        key,
        MetricSemantics(0, key, "Metric direction is not encoded; avoid favorable/best language without manual review."),
    )


def favorable_direction(metric: str) -> int:
    """Return +1, -1 or 0 for higher-is-good, lower-is-good, or unknown/contextual."""
    return semantics_for(metric).direction


def favorable_swing(metric: str, on_minus_off: float) -> float | None:
    """Convert raw ON-minus-OFF into a favorable-direction score when semantics are known.

    Positive result means the ON state moved in the favorable direction. None means the
    metric is intentionally not interpreted as favorable/unfavorable.
    """
    direction = favorable_direction(metric)
    if direction == 0:
        return None
    return float(on_minus_off) * direction


def swing_wording(metric: str, on_minus_off: float) -> str:
    """Safe one-word interpretation for editorial use."""
    score = favorable_swing(metric, on_minus_off)
    if score is None:
        return "direction-neutral"
    if score > 0:
        return "favorable"
    if score < 0:
        return "unfavorable"
    return "neutral"


def self_test() -> None:
    assert favorable_direction("OffRating") == 1
    assert favorable_direction("DefRating") == -1
    assert favorable_direction("Pace") == 0
    assert favorable_direction("UnknownMetric") == 0
    assert favorable_swing("OffRating", 4.2) == 4.2
    assert favorable_swing("DefRating", -4.2) == 4.2
    assert favorable_swing("DefRating", 4.2) == -4.2
    assert favorable_swing("Pace", 2.0) is None
    assert swing_wording("DefRating", -2.5) == "favorable"
    assert swing_wording("Pace", -2.5) == "direction-neutral"


if __name__ == "__main__":
    self_test()
    print("METRIC SEMANTICS SELF-TEST PASSED")
