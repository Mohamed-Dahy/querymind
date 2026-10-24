"""evals/ratelimit.py with a fake clock: bookings, waits, and never over the limits."""

import random
from collections.abc import Callable

import pytest

from evals.ratelimit import WINDOW_SECONDS, Limits, RateLimitedLLM, RateLimiter, Reservation
from querymind.agent.schemas import Clarification
from querymind.llm import LLMProviderError
from querymind.llm.fake import FakeLLMProvider

# headroom=1.0: the numbers in the tests are the limits themselves
LIMITS = Limits(requests_per_minute=3, tokens_per_minute=1000, counts_output=True, headroom=1.0)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def limiter(limits: Limits = LIMITS) -> tuple[RateLimiter, Clock]:
    clock = Clock()
    return RateLimiter(limits, clock=clock), clock


def test_calls_within_the_limits_go_at_once() -> None:
    limit, _ = limiter()
    assert [limit.reserve(100).wait for _ in range(3)] == [0.0, 0.0, 0.0]


def test_one_request_too_many_waits_for_the_oldest_to_leave_the_window() -> None:
    limit, clock = limiter()
    for _ in range(3):
        limit.reserve(10)
    clock.now = 20.0
    assert limit.reserve(10).wait == 40.0  # the first booking (at 0) leaves the window at 60


def test_workers_asking_at_once_get_later_slots() -> None:
    # Three workers book at the same moment: each gets its own minute, none goes over.
    limit, _ = limiter()
    assert [limit.reserve(600).wait for _ in range(3)] == [0.0, 60.0, 120.0]


def test_a_call_bigger_than_the_budget_is_booked_as_a_full_minute() -> None:
    limit, _ = limiter()
    assert limit.reserve(5_000).wait == 0.0  # would never fit: clamped, not stuck forever
    assert limit.reserve(10).wait == 60.0


def test_settling_a_smaller_count_frees_the_rest() -> None:
    limit, _ = limiter()
    booking = limit.reserve(900)
    limit.settle(booking.id, 200)
    assert limit.reserve(700).wait == 0.0


def test_settling_a_bigger_count_makes_later_calls_wait() -> None:
    limit, _ = limiter()
    booking = limit.reserve(100)
    limit.settle(booking.id, 950)
    assert limit.reserve(100).wait == 60.0


def test_old_bookings_are_forgotten() -> None:
    limit, clock = limiter()
    first = limit.reserve(1000)
    clock.now = 61.0
    assert limit.reserve(1000).wait == 0.0
    limit.settle(first.id, 5)  # out of the window already: ignored, no error


def test_headroom_keeps_a_margin_below_the_real_limits() -> None:
    limits = Limits(requests_per_minute=30, tokens_per_minute=8_000, counts_output=True)
    assert (limits.max_requests, limits.max_tokens) == (27, 7_200)


def test_never_over_the_limits_in_any_minute() -> None:
    """Random calls at random moments: every 60-second window stays within both limits."""
    limits = Limits(requests_per_minute=5, tokens_per_minute=2_000, counts_output=True)
    limit, clock = limiter(limits)
    rng = random.Random(42)
    booked: list[tuple[float, int]] = []
    for _ in range(300):
        clock.now += rng.choice([0.0, 0.0, 1.5, 7.0, 30.0])  # often several at once
        tokens = rng.randint(50, 900)
        booked.append((clock.now + limit.reserve(tokens).wait, tokens))

    times = [at for at, _ in booked]
    assert times == sorted(times)  # first come, first served
    for end in times:  # the fullest windows end at a booking
        inside = [tokens for at, tokens in booked if end - WINDOW_SECONDS < at <= end]
        assert len(inside) <= limits.max_requests
        assert sum(inside) <= limits.max_tokens


class Recording:
    """A Limiter that books nothing: it says how long to wait and records what it was told."""

    def __init__(self, wait: float = 0.0) -> None:
        self.wait = wait
        self.reserved: list[int] = []
        self.settled: list[tuple[int, int]] = []

    def reserve(self, tokens: int) -> Reservation:
        self.reserved.append(tokens)
        return Reservation(id=7, wait=self.wait)

    def settle(self, reservation_id: int, tokens: int) -> None:
        self.settled.append((reservation_id, tokens))


def usage(*counts: tuple[int, int]) -> Callable[[], tuple[int, int]]:
    """The (input, output) tokens counted so far: before the call, then after it."""
    return iter(counts).__next__


def test_each_call_waits_its_turn_then_settles_the_real_count() -> None:
    booked, sleeps = Recording(wait=2.5), list[float]()
    llm = RateLimitedLLM(
        FakeLLMProvider(["answer"]),
        booked,
        limits=Limits(30, 8_000, counts_output=True, output_allowance=500),
        used=usage((100, 10), (1_300, 90)),
        sleep=sleeps.append,
    )
    assert llm.generate_text("x" * 400, system="y" * 400) == "answer"
    assert booked.reserved == [200 + 500]  # ~4 characters a token, plus room for the answer
    assert sleeps == [2.5]
    assert booked.settled == [(7, 1_200 + 80)]  # what the provider really counted


def test_input_only_providers_book_and_settle_input_tokens() -> None:
    booked = Recording()
    reply = Clarification(question="Which year?", options=["1997", "1998"])
    llm = RateLimitedLLM(
        FakeLLMProvider([reply]),
        booked,
        limits=Limits(15, 250_000, counts_output=False),
        used=usage((0, 0), (900, 300)),
        sleep=lambda _s: pytest.fail("no wait was asked for"),
    )
    assert llm.generate_structured("x" * 40, Clarification) == reply
    assert (booked.reserved, booked.settled) == ([10], [(7, 900)])


def test_a_failed_call_keeps_its_estimate() -> None:
    booked = Recording()
    llm = RateLimitedLLM(
        FakeLLMProvider([LLMProviderError("429 too many requests")]),
        booked,
        limits=LIMITS,
        used=usage((0, 0), (0, 0)),
    )
    with pytest.raises(LLMProviderError):
        llm.generate_text("q")
    assert booked.settled == []  # nothing counted: the booking stays as estimated
