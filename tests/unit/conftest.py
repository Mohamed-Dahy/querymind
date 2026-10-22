"""Unit tests never read `.env`, so they behave the same on a laptop and in CI.

A test that builds `Settings` but leaves out a required field would pass locally (the
developer's `.env` fills the gap) and fail in CI, where there is no `.env`. That happened in
Phase 2; this fixture makes such a test fail locally too.
"""

import pytest

from querymind.client import ClientSettings
from querymind.config import Settings


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    for settings_class in (Settings, ClientSettings):
        monkeypatch.setitem(settings_class.model_config, "env_file", None)
