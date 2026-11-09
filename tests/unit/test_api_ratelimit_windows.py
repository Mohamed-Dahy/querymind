"""Rate limits (5.6): sliding windows on a fake clock, and the limits the API applies.

Not to be confused with test_ratelimit.py: that one tests the evals' LLM token limiter (4.3)."""

import threading
from uuid import uuid4

import pytest

from querymind.api import ratelimit
from querymind.api.ratelimit import RateLimitedError, RateLimits, Rule, SlidingWindow


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def limits(clock: Clock, **changes: int) -> RateLimits:
    settings = {
        "questions_per_minute": 3,
        "questions_per_day": 5,
        "questions_total_per_day": 8,
        "reruns_per_minute": 2,
        "logins_per_ip_per_minute": 4,
        "logins_per_email_per_minute": 2,
        "signups_per_ip_per_hour": 1,
    } | changes
    return RateLimits(**settings, clock=clock)


def test_a_window_allows_the_limit_then_says_how_long_to_wait() -> None:
    clock = Clock()
    window = SlidingWindow(Rule(limit=2, window_seconds=60), clock=clock)
    window.add("ana")
    clock.now += 10
    window.add("ana")

    assert window.wait("ana") == pytest.approx(50)  # the first hit leaves the window at +60
    assert window.wait("ben") == 0  # each key counts on its own


def test_it_slides_instead_of_resetting_each_minute() -> None:
    # A fixed "per minute" counter would allow 2 at 0:59 and 2 more at 1:00.
    clock = Clock()
    window = SlidingWindow(Rule(limit=2, window_seconds=60), clock=clock)
    clock.now = 59
    window.add("ana")
    window.add("ana")
    clock.now = 61

    assert window.wait("ana") > 0
    clock.now = 119.1
    assert window.wait("ana") == 0


def test_questions_per_user_per_minute() -> None:
    clock = Clock()
    rules = limits(clock)
    user = uuid4()
    for _ in range(3):
        rules.check_question(user)

    with pytest.raises(RateLimitedError) as info:
        rules.check_question(user)
    assert info.value.retry_after_seconds == 60
    assert "Too many questions" in str(info.value)
    rules.check_question(uuid4())  # someone else still can


def test_questions_per_user_per_day() -> None:
    clock = Clock()
    rules = limits(clock)
    user = uuid4()
    for _ in range(5):
        rules.check_question(user)
        clock.now += 61  # never three in a minute

    with pytest.raises(RateLimitedError) as info:
        rules.check_question(user)
    assert info.value.retry_after_seconds > 60 * 60  # hours: the day's first one must age out


def test_a_total_for_everyone_so_many_accounts_cant_add_up() -> None:
    clock = Clock()
    rules = limits(clock)
    for _ in range(8):
        rules.check_question(uuid4())  # 8 different users, one question each

    with pytest.raises(RateLimitedError):
        rules.check_question(uuid4())


def test_a_refused_request_uses_up_nothing() -> None:
    clock = Clock()
    rules = limits(clock, questions_total_per_day=1)
    rules.check_question(uuid4())  # the total is now full
    user = uuid4()
    for _ in range(5):
        with pytest.raises(RateLimitedError):
            rules.check_question(user)

    # If refused tries counted, this user's minute (3) would be full too.
    assert rules._questions_minute.wait(str(user)) == 0


def test_logins_per_email_and_per_address() -> None:
    clock = Clock()
    rules = limits(clock)
    rules.check_login("1.2.3.4", "ana@example.com")
    rules.check_login("5.6.7.8", "ana@example.com")  # guessing Ana's password from two places

    with pytest.raises(RateLimitedError, match="Too many login attempts"):
        rules.check_login("9.9.9.9", "ana@example.com")
    for email in ("b@x.co", "c@x.co", "d@x.co"):
        rules.check_login("1.2.3.4", email)  # one address trying many accounts: 4 with Ana's
    with pytest.raises(RateLimitedError):
        rules.check_login("1.2.3.4", "e@x.co")


def test_signups_and_reruns() -> None:
    clock = Clock()
    rules = limits(clock)
    rules.check_signup("1.2.3.4")
    with pytest.raises(RateLimitedError) as info:
        rules.check_signup("1.2.3.4")
    assert info.value.retry_after_seconds == 3600

    user = uuid4()
    rules.check_rerun(user)
    rules.check_rerun(user)
    with pytest.raises(RateLimitedError):
        rules.check_rerun(user)


def test_many_threads_at_once_never_get_past_the_limit() -> None:
    rules = limits(Clock(), questions_per_minute=10, questions_per_day=100)
    user = uuid4()
    allowed: list[bool] = []

    def ask() -> None:
        try:
            rules.check_question(user)
            allowed.append(True)
        except RateLimitedError:
            allowed.append(False)

    threads = [threading.Thread(target=ask) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert allowed.count(True) == 8  # the total (8) is the tightest limit here


def test_memory_stays_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ratelimit, "MAX_KEYS", 100)
    clock = Clock()
    window = SlidingWindow(Rule(limit=1, window_seconds=60), clock=clock)
    for number in range(1000):
        window.add(f"address-{number}")

    assert len(window._hits) <= 101
