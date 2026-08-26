"""Shared test fixtures.

Every test starts from a clean environment so settings tests stay
order-independent regardless of the machine's real env.
"""

from __future__ import annotations

import pytest

_MANAGED_ENV_KEYS = (
    "PROVIDER",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "CLICKHOUSE_URL",
    "CLICKHOUSE_USER",
    "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_DATABASE",
    "STUDIO_MIND_TRANSPORT",
    "GENERATOR_ROWS",
    "GENERATOR_SEED",
    "GENERATOR_BATCH_SIZE",
    "GENERATOR_OUT_DIR",
    "GENERATOR_LOAD",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in _MANAGED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield
