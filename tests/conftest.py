"""Global test isolation.

Without this, the suite reaches into the developer's real credentials.
`llm_gateway.ensure_credentials_loaded()` calls `load_env_file()` with no
root, which resolves to `~/.config/superrobot/.env`, and copies whatever it
finds into the **process-global** `os.environ` via `setdefault`. Nothing
removes it, so from the first test that touches the analyzer onward:

* every later test sees a real `DATAROBOT_API_TOKEN`;
* `has_llm_credentials()` returns True, so `analyzer.analyze()` builds a real
  `LLMGateway` and issues billed calls against the live platform;
* tests that assert on the unauthenticated code path fail, but only when run
  after the poisoning test -- the order-dependent
  `test_memory_ensure_blocked_without_auth` failure.

The autouse fixture below points the config directory at a per-test tmp path
and clears the credential variables, so tests are hermetic by default and
can never spend a real token. A test that genuinely wants credentials can
still set them explicitly with `monkeypatch.setenv`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

_CREDENTIAL_VARS = (
    "DATAROBOT_ENDPOINT",
    "DATAROBOT_API_TOKEN",
    "SUPERROBOT_MODEL",
    "SUPERROBOT_EVAL_PYTHON",
)


@pytest.fixture(autouse=True)
def isolate_credentials(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Point config at a throwaway dir and drop inherited credentials."""
    config_root = tmp_path_factory.mktemp("superrobot-config")
    monkeypatch.setenv("SUPERROBOT_CONFIG_DIR", str(config_root))
    for var in _CREDENTIAL_VARS:
        monkeypatch.delenv(var, raising=False)
    yield config_root
