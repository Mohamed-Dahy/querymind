"""Re-encrypt every saved secret with the newest key in SECRETS_KEY (5.7).

    uv run python scripts/rotate_secrets.py

To replace the key (e.g. it may have leaked, or once a year):
1. Make a new key and put it **first**: SECRETS_KEY=<new>,<old>
2. Restart the API (new secrets are now saved with the new key; old ones still open with the old).
3. Run this script: every secret is re-encrypted with the new key.
4. Remove the old key: SECRETS_KEY=<new>, and restart the API again.

Runs as the app's role, in one transaction with every row locked, so it is safe while the API runs.
Secrets no key can open are left as they are and listed: their owners must save them again.
"""

import logging
import sys

from querymind.config import ConfigError, get_settings
from querymind.secrets.store import LocalSecretStore
from querymind.store.errors import StoreUnavailableError

logger = logging.getLogger("rotate_secrets")


def main() -> int:
    """Rotate. Returns a process exit code: 0 = all done, 1 = failed or some were unreadable."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        rotated, unreadable = LocalSecretStore(get_settings()).rotate()
    except (ConfigError, StoreUnavailableError) as exc:
        logger.error("Rotating the secrets failed: %s", exc)
        return 1
    logger.info("Re-encrypted %d secrets with the first key in SECRETS_KEY.", rotated)
    if unreadable:
        logger.error(
            "%d secrets no key could open (ids: %s): their owners must save them again.",
            len(unreadable),
            ", ".join(str(secret_id) for secret_id in unreadable),
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
