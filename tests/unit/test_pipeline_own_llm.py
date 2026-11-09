"""A question can bring its own LLM (5.8): every call of that run goes to it, none to ours."""

from querymind.agent.schemas import AskResult
from querymind.llm.fake import FakeLLMProvider
from tests.unit.test_conversation import TOP, StubExecutor, pipeline_with
from tests.unit.test_conversation import executor as executor  # the fixture, re-exported


def test_the_runs_own_llm_writes_the_sql_and_the_summary(executor: StubExecutor) -> None:
    pipeline, ours = pipeline_with()  # nothing queued: any call to ours would fail the run
    theirs = FakeLLMProvider([TOP, "Alfreds Futterkiste leads."])

    reply = pipeline.ask("Top customers?", llm=theirs)

    assert isinstance(reply, AskResult)
    assert reply.answer.text == "Alfreds Futterkiste leads." and reply.answer.from_llm
    assert [call.schema is None for call in theirs.calls] == [False, True]  # SQL, then summary
    assert ours.calls == []


def test_without_one_the_pipelines_own_llm_answers(executor: StubExecutor) -> None:
    pipeline, ours = pipeline_with(TOP)

    reply = pipeline.ask("Top customers?", summarize_answer=False)

    assert isinstance(reply, AskResult)
    assert len(ours.calls) == 1
