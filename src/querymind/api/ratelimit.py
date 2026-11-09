"""Rate limits (5.6): how many questions, re-runs, logins and sign-ups, per user, per IP, in total.

    limits = RateLimits.from_settings(settings)
    limits.check_question(user_id)        # raises RateLimitedError(retry_after_seconds=…) when over

Each limit is a **sliding window**: "at most N in the last W seconds", counted from timestamps, so
there is no burst at the turn of a minute the way a fixed "per calendar minute" counter allows.
A request is checked against all its limits first, and counted in all of them only if every one
has room: a refused request uses up nothing.

Why each one:
- questions per user (minute, day): every question costs LLM calls on our key (until BYOK, 5.8);
- questions for everyone, per day: open sign-up means many accounts could add up to any bill;
- re-runs per user: no LLM, but each runs SQL on the database;
- logins per IP and per email: password guessing, and Argon2id spends ~64 MiB on each attempt;
- sign-ups per IP: creating accounts in bulk.

In this process's memory: right for one server. Several servers (Phase 6) need one shared store
(Redis or Postgres), or each would allow the full limit.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Self
from uuid import UUID

from querymind.config import Settings

MINUTE = 60.0
HOUR = 3600.0
DAY = 86_400.0
MAX_KEYS = 50_000  # a sweep drops idle keys first; past this, the oldest go (memory stays bounded)


class RateLimitedError(Exception):
    """Over a limit. The message is safe to show; `retry_after_seconds` is when one more fits."""

    def __init__(self, message: str, retry_after_seconds: float) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class Rule:
    limit: int
    window_seconds: float


class SlidingWindow:
    """At most `rule.limit` hits per key in any `rule.window_seconds`. Thread-safe."""

    def __init__(self, rule: Rule, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.rule = rule
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def wait(self, key: str) -> float:
        """Seconds until `key` has room for one more (0 = now). Counts nothing."""
        with self._lock:
            hits = self._fresh(key, self._clock())
            if len(hits) < self.rule.limit:
                return 0.0
            return hits[0] + self.rule.window_seconds - self._clock()

    def add(self, key: str) -> None:
        """Count one hit for `key`."""
        with self._lock:
            now = self._clock()
            self._fresh(key, now).append(now)
            if len(self._hits) > MAX_KEYS:
                self._sweep(now)

    def _fresh(self, key: str, now: float) -> deque[float]:
        """The key's hits still inside the window (older ones dropped)."""
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.rule.window_seconds:
            hits.popleft()
        return hits

    def _sweep(self, now: float) -> None:
        for key in [
            k
            for k, hits in self._hits.items()
            if not hits or hits[-1] <= now - self.rule.window_seconds
        ]:
            del self._hits[key]
        while len(self._hits) > MAX_KEYS:  # still too many live keys: forget the oldest
            del self._hits[next(iter(self._hits))]


class RateLimits:
    """Every limit the API applies, from Settings. One object per app (it holds the counts)."""

    def __init__(
        self,
        *,
        questions_per_minute: int,
        questions_per_day: int,
        questions_total_per_day: int,
        reruns_per_minute: int,
        logins_per_ip_per_minute: int,
        logins_per_email_per_minute: int,
        signups_per_ip_per_hour: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Check-then-count over several windows as one step: two requests can't both take the
        # last free place.
        self._lock = threading.Lock()

        def window(limit: int, seconds: float) -> SlidingWindow:
            return SlidingWindow(Rule(limit, seconds), clock=clock)

        self._questions_minute = window(questions_per_minute, MINUTE)
        self._questions_day = window(questions_per_day, DAY)
        self._questions_total = window(questions_total_per_day, DAY)
        self._reruns = window(reruns_per_minute, MINUTE)
        self._logins_ip = window(logins_per_ip_per_minute, MINUTE)
        self._logins_email = window(logins_per_email_per_minute, MINUTE)
        self._signups_ip = window(signups_per_ip_per_hour, HOUR)

    @classmethod
    def from_settings(
        cls, settings: Settings, *, clock: Callable[[], float] = time.monotonic
    ) -> Self:
        return cls(
            questions_per_minute=settings.rate_questions_per_minute,
            questions_per_day=settings.rate_questions_per_day,
            questions_total_per_day=settings.rate_questions_total_per_day,
            reruns_per_minute=settings.rate_reruns_per_minute,
            logins_per_ip_per_minute=settings.rate_logins_per_ip_per_minute,
            logins_per_email_per_minute=settings.rate_logins_per_email_per_minute,
            signups_per_ip_per_hour=settings.rate_signups_per_ip_per_hour,
            clock=clock,
        )

    def _check(self, checks: list[tuple[SlidingWindow, str]], message: str) -> None:
        """All the limits a request falls under: room in every one, or none is counted."""
        with self._lock:
            wait = max(window.wait(key) for window, key in checks)
            if wait > 0:
                raise RateLimitedError(message, retry_after_seconds=math.ceil(wait))
            for window, key in checks:
                window.add(key)

    def check_question(self, user_id: UUID) -> None:
        user = str(user_id)
        self._check(
            [
                (self._questions_minute, user),
                (self._questions_day, user),
                (self._questions_total, "everyone"),
            ],
            "Too many questions for now. Please wait a moment before asking again.",
        )

    def check_rerun(self, user_id: UUID) -> None:
        self._check(
            [(self._reruns, str(user_id))],
            "Too many re-runs in a short time. Please wait a moment.",
        )

    def check_login(self, ip: str, email: str) -> None:
        self._check(
            [(self._logins_ip, ip), (self._logins_email, email)],
            "Too many login attempts. Please wait a moment before trying again.",
        )

    def check_signup(self, ip: str) -> None:
        self._check(
            [(self._signups_ip, ip)],
            "Too many new accounts from here. Please try again later.",
        )
