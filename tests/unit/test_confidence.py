"""score_confidence: each signal's points, the level thresholds, and the reasons shown."""

import pytest

from querymind.agent.confidence import score_confidence
from querymind.agent.schemas import ConfidenceLevel, SanityWarning, SqlAttempt

EMPTY = SanityWarning(code="empty", message="No rows matched.", detail="...")
DOUBLED = SanityWarning(code="duplicate_rows", message="Some rows repeat.", detail="...")
DB_ERROR = SqlAttempt(sql="SELECT x", error_kind="db_error", message="m", detail="d")
SUSPICIOUS = SqlAttempt(sql="SELECT y", error_kind="suspicious", message="m", detail="d")


def score(
    *,
    failed: tuple[SqlAttempt, ...] = (),
    warnings: tuple[SanityWarning, ...] = (),
    truncated: bool = False,
    self_assessment: ConfidenceLevel | None = "high",
) -> tuple[str, int]:
    confidence = score_confidence(
        failed_attempts=failed,
        warnings=warnings,
        truncated=truncated,
        self_assessment=self_assessment,
    )
    return confidence.level, confidence.points


def test_a_clean_first_try_is_high_with_no_reasons() -> None:
    confidence = score_confidence(
        failed_attempts=[], warnings=[], truncated=False, self_assessment="high"
    )
    assert (confidence.level, confidence.points, confidence.reasons) == ("high", 0, ())


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        ({"failed": (DB_ERROR,)}, ("medium", 1)),  # one repair
        ({"failed": (DB_ERROR, DB_ERROR)}, ("medium", 2)),  # two repairs
        ({"failed": (SUSPICIOUS,)}, ("medium", 1)),  # an empty result, fixed by the repair
        ({"warnings": (EMPTY,)}, ("medium", 2)),  # a result that looks off
        ({"truncated": True}, ("medium", 1)),
        ({"self_assessment": "medium"}, ("medium", 1)),
        ({"self_assessment": "low"}, ("medium", 2)),
        ({"self_assessment": None}, ("high", 0)),  # no view given: no penalty
    ],
)
def test_each_signal_alone(signals: dict[str, object], expected: tuple[str, int]) -> None:
    assert score(**signals) == expected  # type: ignore[arg-type]


def test_a_doubt_is_not_counted_twice() -> None:
    # Truly empty ("products never ordered"): repaired once because it was empty, still empty.
    # The warning counts; the repair it caused doesn't count again.
    assert score(failed=(SUSPICIOUS,), warnings=(EMPTY,)) == ("medium", 2)


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        ({"failed": (DB_ERROR,), "warnings": (EMPTY,)}, ("low", 3)),  # repaired AND looks off
        ({"warnings": (EMPTY, DOUBLED)}, ("low", 4)),
        ({"failed": (DB_ERROR,), "self_assessment": "medium"}, ("medium", 2)),
        ({"failed": (DB_ERROR,), "self_assessment": "low"}, ("low", 3)),
        ({"failed": (DB_ERROR, DB_ERROR), "truncated": True}, ("low", 3)),
    ],
)
def test_signals_add_up(signals: dict[str, object], expected: tuple[str, int]) -> None:
    assert score(**signals) == expected  # type: ignore[arg-type]


def test_one_reason_per_signal_in_plain_words() -> None:
    confidence = score_confidence(
        failed_attempts=[DB_ERROR, DB_ERROR],
        warnings=[EMPTY],
        truncated=True,
        self_assessment="low",
    )
    assert confidence.reasons == (
        "It needed 2 repairs to get this result.",
        "No rows matched.",  # the warning's own message
        "The result was cut at the row limit, so the answer may be incomplete.",
        "The AI rated its own understanding of the question as low.",
    )
