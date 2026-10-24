"""Stay under a provider's rate limits: requests and tokens per minute (4.3).

Free tiers allow so many requests and so many tokens per minute (Groq: 30 and 8,000). Going
over gets a 429 error, and the eval waits a minute or more before retrying. With one question
at a time a fixed `--delay` was enough. With several workers running at once (evals/parallel.py)
they need one shared booking system instead: every LLM call books its place first, waits until
its turn, then runs.

The rule is a **sliding window**: within ANY 60 seconds, at most `requests_per_minute` calls
and `tokens_per_minute` tokens. A booking is never placed before an earlier one (first come,
first served), so a call only has to fit into the 60 seconds that end at its own time.

    limiter = RateLimiter(Limits(requests_per_minute=30, tokens_per_minute=8_000))
    booking = limiter.reserve(4_500)     # → Reservation(id=1, wait=0.0): go now
    ... the LLM call ...
    limiter.settle(booking.id, 4_210)    # the real count, so the next bookings are right

No Ray here: the parallel runner wraps this class in a Ray actor, and the tests drive it with a
fake clock.
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from querymind.db.schema import estimate_tokens
from querymind.llm.base import LLMProvider

WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class Limits:
    """A provider's per-minute limits, and how it counts tokens."""

    requests_per_minute: int
    tokens_per_minute: int
    #: Groq counts the answer's tokens too; Gemini only the prompt's ("TPM (input)").
    counts_output: bool
    #: Booked for the answer before its size is known, when output counts (settled after).
    output_allowance: int = 500
    #: Use this share of each limit: token estimates are rough, the provider's count is exact.
    headroom: float = 0.9

    @property
    def max_requests(self) -> int:
        return max(1, int(self.requests_per_minute * self.headroom))

    @property
    def max_tokens(self) -> int:
        return max(1, int(self.tokens_per_minute * self.headroom))


#: Free-tier defaults, used when --rpm / --tpm aren't given. Groq: measured in 3.2 (429 details
#: say TPM). Gemini: an assumption; the real numbers are in AI Studio and change by model.
FREE_TIER = {
    "groq": Limits(requests_per_minute=30, tokens_per_minute=8_000, counts_output=True),
    "gemini": Limits(requests_per_minute=15, tokens_per_minute=250_000, counts_output=False),
}


@dataclass(frozen=True)
class Reservation:
    """A booked call: its id (for settle) and how long to wait before making it."""

    id: int
    wait: float


@dataclass
class _Booking:
    at: float  # when the call may start (clock seconds)
    tokens: int


class RateLimiter:
    """Books LLM calls so that no 60-second window goes over the limits.

    It never sleeps itself: `reserve` answers at once with how long the caller must wait, so
    one shared limiter can serve many workers without blocking any of them.
    """

    def __init__(self, limits: Limits, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.limits = limits
        self._clock = clock
        self._bookings: deque[_Booking] = deque()  # oldest first; `at` never decreases
        self._by_id: dict[int, _Booking] = {}
        self._last_id = 0

    def reserve(self, tokens: int) -> Reservation:
        """Book one call of about `tokens` tokens at the earliest time that fits."""
        now = self._clock()
        self._forget(now)
        # A call bigger than the whole budget would wait forever: book it as a full minute.
        tokens = min(tokens, self.limits.max_tokens)
        at = max(now, self._bookings[-1].at) if self._bookings else now
        for booking in self._bookings:
            if self._fits(at, tokens):
                break
            at = max(at, booking.at + WINDOW_SECONDS)  # wait until this booking leaves the window
        self._last_id += 1
        booking = _Booking(at=at, tokens=tokens)
        self._bookings.append(booking)
        self._by_id[self._last_id] = booking
        return Reservation(id=self._last_id, wait=at - now)

    def settle(self, reservation_id: int, tokens: int) -> None:
        """Replace a booking's estimate with what the call really used."""
        booking = self._by_id.get(reservation_id)
        if booking is not None:  # None: already out of the window, nothing left to correct
            booking.tokens = tokens

    def _fits(self, at: float, tokens: int) -> bool:
        """Would one more call at `at` keep the 60 seconds ending at `at` within the limits?"""
        inside = [b for b in self._bookings if b.at > at - WINDOW_SECONDS]
        return (
            len(inside) + 1 <= self.limits.max_requests
            and sum(b.tokens for b in inside) + tokens <= self.limits.max_tokens
        )

    def _forget(self, now: float) -> None:
        """Drop bookings that no window from now on can contain."""
        while self._bookings and self._bookings[0].at <= now - WINDOW_SECONDS:
            self._bookings.popleft()
        self._by_id = {i: b for i, b in self._by_id.items() if b.at > now - WINDOW_SECONDS}


class Limiter(Protocol):
    """What RateLimitedLLM needs: a RateLimiter here, or the shared Ray actor behind a handle."""

    def reserve(self, tokens: int) -> Reservation: ...

    def settle(self, reservation_id: int, tokens: int) -> None: ...


class RateLimitedLLM(LLMProvider):
    """An LLM that books every call with a limiter, waits its turn, then reports what it used.

    `used` returns the (input, output) tokens counted so far for the current question (the
    worker's AuditCapture, from the "llm call" log lines): the difference before and after a
    call is that call's real usage. A call that failed or reported no usage keeps its estimate.
    """

    def __init__(
        self,
        inner: LLMProvider,
        limiter: Limiter,
        *,
        limits: Limits,
        used: Callable[[], tuple[int, int]],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = inner.name
        self._inner = inner
        self._limiter = limiter
        self._limits = limits
        self._used = used
        self._sleep = sleep

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return self._call(prompt, system, lambda: self._inner.generate_text(prompt, system=system))

    def generate_structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        return self._call(
            prompt, system, lambda: self._inner.generate_structured(prompt, schema, system=system)
        )

    def _call[R](self, prompt: str, system: str | None, call: Callable[[], R]) -> R:
        estimate = estimate_tokens(prompt + (system or ""))
        if self._limits.counts_output:
            estimate += self._limits.output_allowance
        reservation = self._limiter.reserve(estimate)
        if reservation.wait > 0:
            self._sleep(reservation.wait)
        before = self._used()
        try:
            return call()
        finally:
            (in_after, out_after), (in_before, out_before) = self._used(), before
            spent = in_after - in_before
            if self._limits.counts_output:
                spent += out_after - out_before
            if spent > 0:
                self._limiter.settle(reservation.id, spent)
