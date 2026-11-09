"""Which LLM answers a workspace's questions (5.8): its own key, or None for the demo key.

    llms = WorkspaceLLMs(keys, secrets)
    llm = llms.provider_for(user.workspace_id)   # None → the pipeline uses our key from .env
    pipeline.ask(question, llm=llm)

Building a provider means decrypting the key and creating an HTTP client, so each workspace's
provider is kept (least recently used dropped first). It is rebuilt when the workspace saves a new
key or model: every save makes a new secret id, and the cache entry remembers which one it used.
"""

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from uuid import UUID

from pydantic import SecretStr

from querymind.llm.base import LLMProvider
from querymind.llm.catalog import ProviderId
from querymind.llm.factory import create_llm_provider_for
from querymind.secrets.store import SecretStore
from querymind.store.llm_keys import LLMKeyStore

logger = logging.getLogger(__name__)

# Providers kept in memory. Each holds an HTTP client; 256 workspaces asking at once is plenty.
MAX_CACHED = 256

type BuildProvider = Callable[[ProviderId, str, SecretStr], LLMProvider]


class WorkspaceLLMs:
    """Builds and keeps one provider per workspace that saved its own key. Thread-safe."""

    def __init__(
        self,
        keys: LLMKeyStore,
        secrets: SecretStore,
        *,
        build: BuildProvider | None = None,
        temperature: float = 0.0,
        timeout_seconds: float = 60,
    ) -> None:
        self._keys = keys
        self._secrets = secrets
        self._build: BuildProvider = build or (
            lambda provider, model, api_key: create_llm_provider_for(
                provider, model, api_key, temperature=temperature, timeout_seconds=timeout_seconds
            )
        )
        self._cache: OrderedDict[UUID, tuple[UUID, LLMProvider]] = OrderedDict()
        self._lock = threading.Lock()

    def provider_for(self, workspace_id: UUID) -> LLMProvider | None:
        """The workspace's own provider, or None when it has no key (the demo key answers).

        Raises:
            StoreUnavailableError: the app's database failed.
            SecretUnreadableError: the saved key can't be decrypted (it must be saved again).
        """
        record = self._keys.get(workspace_id)  # one small query per question: always current
        with self._lock:
            cached = self._cache.get(workspace_id)
            if record is None:
                self._cache.pop(workspace_id, None)  # deleted: forget it
                return None
            if cached is not None and cached[0] == record.secret_id:
                self._cache.move_to_end(workspace_id)
                return cached[1]
        api_key = self._secrets.reveal(workspace_id, record.secret_id)
        provider = self._build(record.provider, record.model, api_key)
        with self._lock:
            self._cache[workspace_id] = (record.secret_id, provider)
            self._cache.move_to_end(workspace_id)
            while len(self._cache) > MAX_CACHED:
                self._cache.popitem(last=False)
        logger.info(
            "workspace llm ready",
            extra={"workspace_id": str(workspace_id), "provider": record.provider},
        )
        return provider
