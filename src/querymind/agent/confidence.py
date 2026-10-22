"""How sure is QueryMind of an answer? High / Medium / Low, with the reasons. (Phase 2.5)

    confidence = score_confidence(failed_attempts=[db_error], warnings=[], truncated=False,
                                  self_assessment="high")
    confidence.level    # "medium"
    confidence.reasons  # ["It needed 1 repair to get this result."]

Simple penalty points, so every level can be explained (docs/decisions/002-confidence-score.md):

    each repair ................... 1   a query failed; the fix may be only partly right
    each sanity warning ........... 2   the result itself looks off (no rows, duplicates...)
    result truncated .............. 1   the answer is based on part of the rows
    LLM self-assessment medium/low  1/2 the LLM wasn't sure it understood the question

    0 points = high · 1-2 = medium · 3 or more = low
"""

from collections.abc import Sequence

from querymind.agent.schemas import Confidence, ConfidenceLevel, SanityWarning, SqlAttempt

REPAIR_POINTS = 1
WARNING_POINTS = 2
TRUNCATED_POINTS = 1
SELF_ASSESSMENT_POINTS: dict[ConfidenceLevel, int] = {"high": 0, "medium": 1, "low": 2}
MEDIUM_FROM = 1  # this many points or more = medium
LOW_FROM = 3  # this many points or more = low


def score_confidence(
    *,
    failed_attempts: Sequence[SqlAttempt],
    warnings: Sequence[SanityWarning],
    truncated: bool,
    self_assessment: ConfidenceLevel | None,
) -> Confidence:
    """Combine the signals into a level, keeping one reason per signal that lowered it.

    A "suspicious" attempt (a repair asked for because the result looked wrong) isn't counted
    while that result's warning is still shown: the warning already counts that doubt.
    """
    points = 0
    reasons: list[str] = []
    repairs = [a for a in failed_attempts if a.error_kind != "suspicious" or not warnings]
    if repairs:
        points += REPAIR_POINTS * len(repairs)
        plural = "s" if len(repairs) > 1 else ""
        reasons.append(f"It needed {len(repairs)} repair{plural} to get this result.")
    for warning in warnings:
        points += WARNING_POINTS
        reasons.append(warning.message)
    if truncated:
        points += TRUNCATED_POINTS
        reasons.append("The result was cut at the row limit, so the answer may be incomplete.")
    if self_assessment in ("medium", "low"):
        points += SELF_ASSESSMENT_POINTS[self_assessment]
        reasons.append(f"The AI rated its own understanding of the question as {self_assessment}.")
    return Confidence(level=_level(points), reasons=tuple(reasons), points=points)


def _level(points: int) -> ConfidenceLevel:
    if points >= LOW_FROM:
        return "low"
    return "medium" if points >= MEDIUM_FROM else "high"
