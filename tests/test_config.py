"""Tests for the typed settings layer."""

from __future__ import annotations

import textwrap

from studio_mind.config import load_settings


def write_env(tmp_path, body: str) -> object:
    env_file = tmp_path / ".env"
    env_file.write_text(textwrap.dedent(body), encoding="utf-8")
    return env_file


def test_defaults_match_documented_shapes(tmp_path):
    settings = load_settings(env_file=tmp_path / "missing.env")
    assert settings.llm.provider == "gemini"
    assert settings.llm.model == "gemini-2.5-flash"
    assert settings.ch.url == "http://localhost:8123"
    assert settings.ch.database == "studio"
    assert settings.ch.transport == "mcp"  # official mcp-clickhouse is the runtime default
    assert settings.generator.rows == 50_000_000
    assert settings.generator.seed == 20260826
    assert settings.generator.load is True


def test_env_file_overrides(tmp_path):
    env_file = write_env(
        tmp_path,
        """
        PROVIDER=vertex
        GEMINI_MODEL=gemini-2.5-pro
        GOOGLE_CLOUD_PROJECT=studio-mind-prod
        CLICKHOUSE_URL=https://abc.us-east-1.aws.clickhouse.cloud:8443
        CLICKHOUSE_PASSWORD=sekrit
        STUDIO_MIND_TRANSPORT=http
        GENERATOR_ROWS=200000
        GENERATOR_LOAD=false
        """,
    )
    settings = load_settings(env_file=env_file)
    assert settings.llm.is_vertex is True
    assert settings.llm.google_cloud_project == "studio-mind-prod"
    assert settings.llm.model == "gemini-2.5-pro"
    assert settings.ch.url.endswith(":8443")
    assert settings.ch.is_secure is True
    assert settings.ch.password == "sekrit"
    assert settings.ch.transport == "http"
    assert settings.generator.rows == 200_000
    assert settings.generator.load is False


def test_with_overrides_returns_new_settings(tmp_path):
    settings = load_settings(env_file=tmp_path / "missing.env")
    swapped = settings.with_overrides(ch__url="https://cloud.example:8443", ch__database="demo")
    assert swapped.ch.url == "https://cloud.example:8443"
    assert swapped.ch.database == "demo"
    # original untouched (frozen dataclasses)
    assert settings.ch.url == "http://localhost:8123"
    assert swapped.llm is settings.llm


def test_with_overrides_rejects_malformed_key(tmp_path):
    settings = load_settings(env_file=tmp_path / "missing.env")
    try:
        settings.with_overrides(url="http://oops")
    except ValueError as exc:
        assert "ch__url" in str(exc)
    else:
        raise AssertionError("expected ValueError for key without section")
