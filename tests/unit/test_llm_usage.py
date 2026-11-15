"""5.8b: which model answered, on whose key, its tokens and cost. No DB, no network.

The meter and the wrapper alone, the price list, and the done-when on a whole run: an answer on
a fake provider reports its tokens and cost; an unknown model reports tokens, no cost.
"""

import logging
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AskResult, ClarificationRequest
from querymind.config import Settings
from querymind.llm import LLMProviderError
from querymind.llm.fake import FakeLLMProvider, FakeResponse
from querymind.llm.prices import Price, Prices, cost_usd, known_prices
from querymind.llm.usage import MeteredLLM, UsageMeter
from tests.unit.test_conversation import ASK_BACK, TOP, StubExecutor
from tests.unit.test_conversation import executor as executor  # the fixture, re-exported

PRICES = Prices.model_validate(
    {
        "checked": "2026-11-15",
        "models": {"fake / priced-model": Price(input=1.0, output=2.0, source="test")},
    }
)
SETTINGS: dict[str, Any] = {
    "postgres_host": "127.0.0.1",
    "postgres_port": 5433,
    "postgres_db": "northwind",
    "postgres_admin_user": "admin",
    "postgres_admin_password": SecretStr("x"),
    "analyst_ro_user": "analyst_ro",
    "analyst_ro_password": SecretStr("x"),
    "llm_provider": "groq",
    "groq_api_key": SecretStr("gsk_test"),
    "groq_model": "openai/gpt-oss-120b",
    "semantic_layer_paths": [],
}


def pipeline_for(llm: FakeLLMProvider) -> Pipeline:
    return Pipeline(
        llm=llm, settings=Settings.model_validate(SETTINGS), connect=MagicMock(), prices=PRICES
    )


def fake(*responses: FakeResponse, model: str = "priced-model") -> FakeLLMProvider:
    return FakeLLMProvider(responses, model=model, usage=(1000, 200))


# --- The meter and the wrapper ---


def test_the_meter_adds_up_calls_and_tokens() -> None:
    meter = UsageMeter()
    meter.add(100, 20)
    meter.add(50, 5)

    assert (meter.calls, meter.input_tokens, meter.output_tokens) == (2, 150, 25)


def test_one_call_without_counts_makes_the_totals_unknown_not_smaller() -> None:
    meter = UsageMeter()
    meter.add(100, 20)
    meter.add(None, None)

    assert meter.calls == 2
    assert meter.input_tokens is None and meter.output_tokens is None


def test_the_meter_is_safe_from_several_threads() -> None:
    meter = UsageMeter()

    def add_many() -> None:
        for _ in range(1000):
            meter.add(1, 1)

    threads = [threading.Thread(target=add_many) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert (meter.calls, meter.input_tokens) == (8000, 8000)


def test_a_failed_call_is_still_counted() -> None:
    """A reply that didn't fit, or a provider error after the model ran, was paid for."""
    meter = UsageMeter()
    llm = MeteredLLM(FakeLLMProvider([LLMProviderError("down")], usage=(30, 0)), meter)

    with pytest.raises(LLMProviderError):
        llm.generate_text("hello")

    assert (meter.calls, meter.input_tokens) == (1, 30)


def test_the_wrapper_keeps_the_providers_name_and_model() -> None:
    llm = MeteredLLM(FakeLLMProvider(model="some-model"), UsageMeter())

    assert (llm.name, llm.model) == ("fake", "some-model")


# --- Prices ---


def test_the_price_is_input_and_output_at_their_rates() -> None:
    # 1,000 in at $1/M + 200 out at $2/M = $0.0014.
    assert cost_usd(PRICES, "fake", "priced-model", 1000, 200) == 0.0014


@pytest.mark.parametrize(
    ("model", "tokens"),
    [("unknown-model", (1000, 200)), ("priced-model", (None, None))],
    ids=["model not listed", "tokens unknown"],
)
def test_no_price_is_none_never_a_guess(model: str, tokens: tuple[int | None, int | None]) -> None:
    assert cost_usd(PRICES, "fake", model, *tokens) is None


def test_the_package_list_knows_the_models_we_evaluated() -> None:
    prices = known_prices()

    assert "groq / openai/gpt-oss-20b" in prices.models
    assert cost_usd(prices, "groq", "openai/gpt-oss-20b", 1_000_000, 0) == 0.075


# --- A whole run (the done-when) ---


def test_an_answer_on_a_fake_provider_reports_its_tokens_and_cost(
    executor: StubExecutor,
) -> None:
    llm = fake(TOP, "There are 2 customers.")  # the SQL, then the summary
    reply = pipeline_for(llm).ask("Top customers?")

    assert isinstance(reply, AskResult)
    usage = reply.usage
    assert usage is not None
    assert (usage.provider, usage.model, usage.own_key) == ("fake", "priced-model", False)
    assert (usage.llm_calls, usage.input_tokens, usage.output_tokens) == (2, 2000, 400)
    assert usage.cost_usd == 0.0028  # 2,000 x $1/M + 400 x $2/M


def test_an_unknown_model_reports_tokens_and_no_cost(executor: StubExecutor) -> None:
    reply = pipeline_for(fake(TOP, "Two.", model="not-in-the-list")).ask("Top customers?")

    assert reply.usage is not None
    assert (reply.usage.input_tokens, reply.usage.cost_usd) == (2000, None)


def test_the_workspaces_own_key_is_named_as_such(executor: StubExecutor) -> None:
    own = fake(TOP, "Two.", model="own-model")
    reply = pipeline_for(fake()).ask("Top customers?", llm=own)

    assert reply.usage is not None
    assert (reply.usage.model, reply.usage.own_key) == ("own-model", True)


def test_asking_back_reports_what_it_cost_too(executor: StubExecutor) -> None:
    reply = pipeline_for(fake(ASK_BACK)).ask("Best customers?")

    assert isinstance(reply, ClarificationRequest)
    assert reply.usage is not None and reply.usage.llm_calls == 1


def test_each_run_counts_only_its_own_calls(executor: StubExecutor) -> None:
    pipeline = pipeline_for(fake(TOP, "One.", TOP, "Two."))

    first, second = pipeline.ask("q1"), pipeline.ask("q2")

    assert first.usage is not None and second.usage is not None
    assert first.usage.llm_calls == second.usage.llm_calls == 2  # never 4: no shared counter


def test_the_audit_line_names_the_model_and_its_cost(
    executor: StubExecutor, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="querymind.audit"):
        pipeline_for(fake(TOP, "Two.")).ask("Top customers?")

    (line,) = [r for r in caplog.records if r.name == "querymind.audit"]
    assert (line.model, line.own_key, line.llm_calls) == ("priced-model", False, 2)  # type: ignore[attr-defined]
    assert (line.input_tokens, line.cost_usd) == (2000, 0.0028)  # type: ignore[attr-defined]
