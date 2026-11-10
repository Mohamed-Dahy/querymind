"""Which pipeline answers a question (5.11): the demo's, or one per saved database.

    registry = PipelineRegistry(demo, connections, settings=settings, history_loader=...)
    pipeline = registry.for_database(workspace_id, connection_id)   # None → the demo pipeline
    pipeline.ask(question, conversation_id=..., llm=workspace_llm)

A `Pipeline` knows one database: its schema (read on the first question), its semantic layers,
its conversation memory and run locks. So each saved database gets its own, built on first use and
kept (the least recently used dropped first). It connects through `db/remote.connect_remote`: the
SSRF guard runs at every connect and every transaction is READ ONLY.

A user database's pipeline has **no LLM of its own**: its built-in one refuses, so a question there
can only run on the workspace's own key, passed in each call (ADR 010: own database, own key).
"""

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from uuid import UUID

import psycopg
from pydantic import BaseModel, SecretStr

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AnswerSource, Turn
from querymind.config import Settings
from querymind.db.remote import connect_remote
from querymind.llm import LLMProvider, LLMProviderError
from querymind.secrets.errors import SecretUnreadableError
from querymind.store.connections import ConnectionRecord, ConnectionStore
from querymind.store.errors import ConnectionUnreadableError, StoreUnavailableError

logger = logging.getLogger(__name__)

# Pipelines of users' databases kept in memory. Each holds a schema and up to 100 conversations.
MAX_PIPELINES = 32


class NoOwnLLM(LLMProvider):
    """The built-in LLM of a user database's pipeline: it never answers. The workspace's own key
    is passed with each question; if a bug ever forgot it, this refuses rather than our key."""

    name = "none"

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        raise LLMProviderError("No LLM key for this database: the workspace's own key is needed.")

    def generate_structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        raise LLMProviderError("No LLM key for this database: the workspace's own key is needed.")


def database_settings(settings: Settings) -> Settings:
    """The settings of a user database's pipeline: no YAML semantic layers (they describe the demo)
    and no schema retrieval (its index is built by onboarding, 5.10). The whole schema is sent."""
    return settings.model_copy(update={"semantic_layer_paths": [], "schema_retrieval": "off"})


type BuildPipeline = Callable[[ConnectionRecord, SecretStr], Pipeline]


class OwnKeyRequiredError(Exception):
    """A question on the workspace's own database, but the workspace has no LLM key (ADR 010:
    own database, own key; our key would pay for, and see, a stranger's data). Safe to show."""


class PipelineRegistry:
    """The demo pipeline, and one pipeline per saved database. Thread-safe."""

    def __init__(
        self,
        demo: Pipeline,
        connections: ConnectionStore | None,
        *,
        settings: Settings,
        allow_private: bool = False,
        history_loader: Callable[[str], list[Turn]] | None = None,
        build: BuildPipeline | None = None,
    ) -> None:
        self.demo = demo
        self._connections = connections
        self._settings = database_settings(settings)
        self._allow_private = allow_private
        self._history_loader = history_loader
        self._build: BuildPipeline = build or self._new_pipeline
        # connection id → (the secret id it was built with, the pipeline)
        self._cache: OrderedDict[UUID, tuple[UUID, Pipeline]] = OrderedDict()
        self._lock = threading.Lock()

    def for_database(self, workspace_id: UUID, connection_id: UUID | None) -> Pipeline:
        """The pipeline for this database: None = the demo.

        Raises:
            ConnectionNotFoundError: not one of this workspace's databases.
            ConnectionUnreadableError: its saved password can't be decrypted.
            StoreUnavailableError: the app's database failed.
        """
        if connection_id is None:
            return self.demo
        if self._connections is None:
            raise StoreUnavailableError("Saved databases aren't available on this server.")
        record = self._connections.get(workspace_id, connection_id)  # 404 for another workspace's
        # Built under the lock: two pipelines for one database would mean two memories and two sets
        # of run locks for the same conversation. Building is cheap: the schema is read later.
        with self._lock:
            cached = self._cache.get(connection_id)
            if cached is not None and cached[0] == record.secret_id:
                self._cache.move_to_end(connection_id)
                return cached[1]
            try:
                password = self._connections.password(workspace_id, record)
            except SecretUnreadableError:
                raise ConnectionUnreadableError(
                    "This database's saved password can't be read: delete it and add it again."
                ) from None
            pipeline = self._build(record, password)
            self._cache[connection_id] = (record.secret_id, pipeline)
            while len(self._cache) > MAX_PIPELINES:
                self._cache.popitem(last=False)
        logger.info("database pipeline ready", extra={"connection_id": str(connection_id)})
        return pipeline

    def forget_conversation(self, connection_id: UUID | None, conversation_id: str) -> None:
        """Drop a deleted conversation from its database's memory. A database whose pipeline
        isn't built has nothing to forget: nothing is built for this."""
        if connection_id is None:
            self.demo.forget(conversation_id)
            return
        with self._lock:
            cached = self._cache.get(connection_id)
        if cached is not None:
            cached[1].forget(conversation_id)

    def forget(self, connection_id: UUID) -> None:
        """Drop a database's pipeline: it was deleted, or tested again (its schema may differ)."""
        with self._lock:
            self._cache.pop(connection_id, None)

    def _new_pipeline(self, record: ConnectionRecord, password: SecretStr) -> Pipeline:
        spec = record.spec()

        def connect(_settings: Settings) -> psycopg.Connection:
            # connect_remote: the SSRF guard at every connect, READ ONLY transactions.
            return connect_remote(spec, password, allow_private=self._allow_private)

        return Pipeline(
            llm=NoOwnLLM(),
            settings=self._settings,
            connect=connect,
            history_loader=self._history_loader,
            source=AnswerSource(
                database=record.name,
                description=f"Your database {record.dbname} on {record.host}.",
            ),
        )
