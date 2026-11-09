"""Bring your own LLM key (5.8): the key check, each workspace's provider, and the /llm routes.

The PLAN's "done when": two users with different keys get answers from their own providers
(fakes here), a bad key is refused at save time with a clear message, and no reply ever holds
the key. No real provider, no database.
"""

import json
from collections.abc import Generator, Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from querymind.agent.events import AgentEvent, Finished
from querymind.agent.schemas import AgentReply
from querymind.api import llm_keys as llm_keys_routes
from querymind.api.main import create_app
from querymind.api.ratelimit import RateLimits
from querymind.auth.tokens import AuthUser
from querymind.llm import LLMAuthError, LLMOutputError, LLMProvider, LLMProviderError
from querymind.llm.catalog import ProviderId, entry_for
from querymind.llm.errors import LLMRateLimitError
from querymind.llm.fake import FakeLLMProvider
from querymind.llm.key_check import KeyCheckAnswer, KeyCheckError, check_key
from querymind.llm.workspace import WorkspaceLLMs
from querymind.secrets.errors import SecretNotFoundError, SecretUnreadableError
from querymind.store.audit import AuditEntry
from querymind.store.llm_keys import LLMKeyRecord
from tests.auth_helpers import OTHER_USER, TEST_USER, signed_in
from tests.unit.test_api import RESULT

GROQ_KEY = "gsk_live_0123456789abcdefghijklmnopWXYZ"
CLAUDE_KEY = "sk-ant-api03-0123456789abcdefghijABCD"


# --- Fakes: the key store and secrets store in memory -----------------------------------------


class MemoryKeys:
    """Stands in for LLMKeyStore and the secrets store at once (what WorkspaceLLMs needs)."""

    def __init__(self) -> None:
        self.records: dict[UUID, LLMKeyRecord] = {}
        self.secrets: dict[UUID, str] = {}
        self.reveals = 0

    def get(self, workspace_id: UUID) -> LLMKeyRecord | None:
        return self.records.get(workspace_id)

    def save(
        self, workspace_id: UUID, provider: ProviderId, model: str, api_key: SecretStr
    ) -> LLMKeyRecord:
        secret_id = uuid4()  # a new secret on every save, as the real store does
        self.secrets[secret_id] = api_key.get_secret_value()
        record = LLMKeyRecord(
            provider=provider,
            model=model,
            secret_id=secret_id,
            hint=api_key.get_secret_value()[-4:],
            updated_at=datetime.now(UTC),
        )
        self.records[workspace_id] = record
        return record

    def delete(self, workspace_id: UUID) -> bool:
        return self.records.pop(workspace_id, None) is not None

    def reveal(self, workspace_id: UUID, secret_id: UUID) -> SecretStr:
        self.reveals += 1
        record = self.records.get(workspace_id)
        if record is None or record.secret_id != secret_id:
            raise SecretNotFoundError("No saved secret with this id.")
        return SecretStr(self.secrets[secret_id])


class NamedLLM(FakeLLMProvider):
    """A fake provider that remembers which provider and key built it."""

    def __init__(self, provider: str, key: SecretStr) -> None:
        super().__init__()
        self.name = provider
        self.key = key.get_secret_value()


def build(provider: ProviderId, model: str, api_key: SecretStr) -> LLMProvider:
    return NamedLLM(provider, api_key)


class RecordingPipeline:
    """Answers every question and records which LLM it was asked to use."""

    def __init__(self) -> None:
        self.llms: list[LLMProvider | None] = []

    def ask(
        self, question: str, *, conversation_id: str | None = None, llm: LLMProvider | None = None
    ) -> AgentReply:
        self.llms.append(llm)
        return RESULT

    def stream(
        self, question: str, *, conversation_id: str | None = None, llm: LLMProvider | None = None
    ) -> Generator[AgentEvent | Finished]:
        self.llms.append(llm)
        yield Finished(RESULT)


class UnreadableKeys(MemoryKeys):
    """A saved key that can't be decrypted (e.g. SECRETS_KEY was changed without rotating)."""

    def reveal(self, workspace_id: UUID, secret_id: UUID) -> SecretStr:
        raise SecretUnreadableError("A saved secret can't be read: save it again.")


class MemoryAudit:
    def __init__(self) -> None:
        self.lines: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        self.lines.append(entry)


# --- The key check ----------------------------------------------------------------------------


def test_a_working_key_passes_with_one_tiny_structured_call() -> None:
    provider = FakeLLMProvider([KeyCheckAnswer(answer=5)])

    check_key(provider, entry_for("groq"))

    assert len(provider.calls) == 1 and provider.calls[0].schema is KeyCheckAnswer


@pytest.mark.parametrize(
    ("error", "says"),
    [
        (LLMAuthError(f"groq refused the API key: {GROQ_KEY}"), "Groq refused this key"),
        (LLMRateLimitError("429 out of credit"), "over its limit or out of credit"),
        (LLMOutputError("tool_use_failed"), "can't return structured answers"),
        (LLMProviderError("404 model not found"), "Check the model name"),
    ],
)
def test_each_failure_gets_a_clear_message_without_the_providers_reply(
    error: Exception, says: str
) -> None:
    with pytest.raises(KeyCheckError) as refused:
        check_key(FakeLLMProvider([error]), entry_for("groq"))

    assert says in str(refused.value)
    assert GROQ_KEY not in str(refused.value)
    assert str(error) not in str(refused.value)  # our wording, never the vendor's text


# --- Each workspace's provider ----------------------------------------------------------------


def test_no_key_means_the_demo_key() -> None:
    keys = MemoryKeys()

    assert WorkspaceLLMs(keys, keys, build=build).provider_for(TEST_USER.workspace_id) is None  # type: ignore[arg-type]


def test_a_workspaces_provider_is_built_once_and_rebuilt_after_a_new_key() -> None:
    keys = MemoryKeys()
    llms = WorkspaceLLMs(keys, keys, build=build)  # type: ignore[arg-type]
    workspace = TEST_USER.workspace_id
    keys.save(workspace, "groq", "openai/gpt-oss-120b", SecretStr(GROQ_KEY))

    first = llms.provider_for(workspace)
    again = llms.provider_for(workspace)
    assert first is again and keys.reveals == 1  # decrypted once, then kept

    keys.save(workspace, "anthropic", "claude-haiku-4-5", SecretStr(CLAUDE_KEY))
    changed = llms.provider_for(workspace)
    assert isinstance(changed, NamedLLM) and (changed.name, changed.key) == (
        "anthropic",
        CLAUDE_KEY,
    )

    keys.delete(workspace)
    assert llms.provider_for(workspace) is None  # deleted: back to the demo key at once


# --- The routes --------------------------------------------------------------------------------


def app_with(
    keys: MemoryKeys,
    pipeline: RecordingPipeline | None = None,
    audit: MemoryAudit | None = None,
    limits: RateLimits | None = None,
) -> FastAPI:
    return create_app(
        pipeline=pipeline or RecordingPipeline(),
        llm_keys=keys,  # type: ignore[arg-type]
        workspace_llms=WorkspaceLLMs(keys, keys, build=build),  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        limits=limits,
    )


@pytest.fixture
def check_answers(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[object]]:
    """What the key check's provider will answer: queue a KeyCheckAnswer or an error."""
    queued: list[object] = []

    def fake_provider(*_args: object, **_kwargs: object) -> LLMProvider:
        return FakeLLMProvider(queued)  # type: ignore[arg-type]

    monkeypatch.setattr(llm_keys_routes, "create_llm_provider_for", fake_provider)
    yield queued


def save(client: TestClient, **fields: object) -> object:
    body = {"provider": "groq", "model": "openai/gpt-oss-120b", "api_key": GROQ_KEY} | fields
    return client.put("/llm/key", json=body)


def test_the_provider_list_is_the_fixed_catalog() -> None:
    with TestClient(signed_in(app_with(MemoryKeys()))) as client:
        listed = client.get("/llm/providers").json()

    assert [p["id"] for p in listed] == [
        "groq",
        "gemini",
        "openai",
        "openrouter",
        "deepseek",
        "together",
        "anthropic",
    ]
    assert "base_url" not in listed[0]  # nothing to type an address into


def test_saving_a_working_key_returns_its_hint_never_the_key(check_answers: list[object]) -> None:
    check_answers.append(KeyCheckAnswer(answer=5))
    keys, audit = MemoryKeys(), MemoryAudit()
    with TestClient(signed_in(app_with(keys, audit=audit))) as client:
        saved = save(client)
        shown = client.get("/llm/key")

    assert saved.status_code == 200  # type: ignore[attr-defined]
    for response in (saved, shown):
        assert GROQ_KEY not in response.text  # type: ignore[attr-defined]
        assert response.json()["key_hint"] == "WXYZ"  # type: ignore[attr-defined]
        assert response.json()["provider_label"] == "Groq"  # type: ignore[attr-defined]
    assert keys.secrets  # saved
    [line] = audit.lines
    assert (line.action, line.outcome, line.detail) == (
        "llm_key_saved",
        "ok",
        "groq / openai/gpt-oss-120b",
    )
    assert GROQ_KEY not in line.model_dump_json()


def test_a_refused_key_is_not_saved_and_the_reply_says_why(check_answers: list[object]) -> None:
    check_answers.append(LLMAuthError(f"groq refused the API key: 401 {GROQ_KEY}"))
    keys, audit = MemoryKeys(), MemoryAudit()
    with TestClient(signed_in(app_with(keys, audit=audit))) as client:
        refused = save(client)

    assert refused.status_code == 422  # type: ignore[attr-defined]
    assert refused.json()["error"] == "llm_key_invalid"  # type: ignore[attr-defined]
    assert "Groq refused this key" in refused.json()["message"]  # type: ignore[attr-defined]
    assert GROQ_KEY not in refused.text  # type: ignore[attr-defined]
    assert keys.records == {} and keys.secrets == {}
    assert [(line.action, line.outcome) for line in audit.lines] == [
        ("llm_key_refused", "llm_key_invalid")
    ]


@pytest.mark.parametrize(
    "fields",
    [
        {"api_key": GROQ_KEY + "x" * 600},  # too long: never sent on to a provider
        {"model": "gpt-4 ; DROP TABLE"},  # not a model id
        {"provider": "my-own-server"},  # not in the catalog
        {"base_url": "http://169.254.169.254/latest"},  # no such field: no address to type
    ],
)
def test_a_bad_request_is_refused_without_echoing_the_key(
    check_answers: list[object], fields: dict[str, object]
) -> None:
    with TestClient(signed_in(app_with(MemoryKeys()))) as client:
        refused = save(client, **fields)

    assert refused.status_code == 422  # type: ignore[attr-defined]
    assert refused.json()["error"] == "invalid_request"  # type: ignore[attr-defined]
    assert GROQ_KEY not in refused.text  # type: ignore[attr-defined]
    assert check_answers == []  # no call made


def test_only_an_admin_can_save_or_delete_a_key(check_answers: list[object]) -> None:
    analyst = AuthUser(
        user_id=uuid4(), workspace_id=TEST_USER.workspace_id, role="analyst", email="a@x.com"
    )
    with TestClient(signed_in(app_with(MemoryKeys()), analyst)) as client:
        assert save(client).status_code == 403  # type: ignore[attr-defined]
        assert client.delete("/llm/key").status_code == 403
        assert client.get("/llm/key").status_code == 200  # but they may see which one is used


def test_key_checks_are_rate_limited(check_answers: list[object]) -> None:
    """Each save calls the provider with the key: the server can't become a key tester."""
    check_answers.extend(LLMAuthError("401") for _ in range(5))
    limits = RateLimits(
        questions_per_minute=10,
        questions_per_day=10,
        questions_total_per_day=10,
        reruns_per_minute=10,
        logins_per_ip_per_minute=10,
        logins_per_email_per_minute=10,
        signups_per_ip_per_hour=10,
        key_checks_per_hour=2,
    )
    with TestClient(signed_in(app_with(MemoryKeys(), limits=limits))) as client:
        statuses = [save(client).status_code for _ in range(3)]  # type: ignore[attr-defined]

    assert statuses == [422, 422, 429]


def test_deleting_the_key_goes_back_to_the_demo_key(check_answers: list[object]) -> None:
    keys, audit, pipeline = MemoryKeys(), MemoryAudit(), RecordingPipeline()
    keys.save(TEST_USER.workspace_id, "groq", "openai/gpt-oss-120b", SecretStr(GROQ_KEY))
    with TestClient(signed_in(app_with(keys, pipeline, audit))) as client:
        assert client.delete("/llm/key").status_code == 204
        assert client.get("/llm/key").json() is None
        client.post("/ask", json={"question": "How many customers?"})
        assert client.delete("/llm/key").status_code == 204  # already gone: still fine

    assert pipeline.llms == [None]
    # Written once (the second delete found nothing); the question has its own line.
    assert [line.action for line in audit.lines] == ["llm_key_deleted", "ask"]


# --- Done when: two users, two keys, two providers --------------------------------------------


def test_two_workspaces_get_answers_from_their_own_providers() -> None:
    keys, pipeline = MemoryKeys(), RecordingPipeline()
    keys.save(TEST_USER.workspace_id, "groq", "openai/gpt-oss-120b", SecretStr(GROQ_KEY))
    keys.save(OTHER_USER.workspace_id, "anthropic", "claude-haiku-4-5", SecretStr(CLAUDE_KEY))
    third = AuthUser(user_id=uuid4(), workspace_id=uuid4(), role="admin", email="c@x.com")
    app = app_with(keys, pipeline)

    with TestClient(app) as client:
        for user in (TEST_USER, OTHER_USER, third):
            signed_in(app, user)
            assert client.post("/ask", json={"question": "How many customers?"}).status_code == 200

    groq, claude, demo = pipeline.llms
    assert isinstance(groq, NamedLLM) and (groq.name, groq.key) == ("groq", GROQ_KEY)
    assert isinstance(claude, NamedLLM) and (claude.name, claude.key) == ("anthropic", CLAUDE_KEY)
    assert demo is None  # no key saved: the demo key in .env answers


def test_the_stream_also_runs_on_the_workspaces_own_provider() -> None:
    keys, pipeline = MemoryKeys(), RecordingPipeline()
    keys.save(TEST_USER.workspace_id, "groq", "openai/gpt-oss-120b", SecretStr(GROQ_KEY))
    with TestClient(signed_in(app_with(keys, pipeline))) as client:
        response = client.post("/ask/stream", json={"question": "How many customers?"})

    assert response.status_code == 200
    [llm] = pipeline.llms
    assert isinstance(llm, NamedLLM) and llm.name == "groq"


# --- An unreadable key still gets the question's audit line (§8.7) -----------------------------


def unreadable_app(audit: MemoryAudit) -> FastAPI:
    keys = UnreadableKeys()
    keys.save(TEST_USER.workspace_id, "groq", "openai/gpt-oss-120b", SecretStr(GROQ_KEY))
    return signed_in(app_with(keys, audit=audit))


def test_ask_with_an_unreadable_key_is_a_409_with_its_audit_line() -> None:
    audit = MemoryAudit()
    with TestClient(unreadable_app(audit)) as client:
        response = client.post("/ask", json={"question": "How many customers?"})

    assert response.status_code == 409
    assert response.json()["error"] == "llm_key_unreadable"
    [line] = audit.lines
    assert (line.action, line.outcome, line.question) == (
        "ask",
        "llm_key_unreadable",
        "How many customers?",
    )


def test_the_stream_with_an_unreadable_key_ends_in_an_error_event_with_its_audit_line() -> None:
    audit = MemoryAudit()
    with TestClient(unreadable_app(audit)) as client:
        response = client.post("/ask/stream", json={"question": "How many customers?"})

    events = [
        json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")
    ]
    assert events[-1]["type"] == "error"
    assert events[-1]["error"]["error"] == "llm_key_unreadable"
    assert [(line.action, line.outcome) for line in audit.lines] == [("ask", "llm_key_unreadable")]
