"""GOOGLE_CREDENTIALS_JSON adapter (config.vertex_credentials) + the Vercel
function entry (api/index.py) — offline tests, no network.

The adapter is what lets the service run on Vercel with no key file: the
service-account JSON arrives as one env-var string and becomes in-memory
credentials. Precedence must hold: env JSON > key file > None (ADC).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from studio_mind.config import vertex_credentials


def _fake_sa_info() -> dict:
    """Service-account info with a real (throwaway) RSA key so
    from_service_account_info can build a working signer offline."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

    key = _rsa.generate_private_key(public_exponent=65537, key_size=1024)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {
        "type": "service_account",
        "project_id": "test-project",
        "private_key_id": "abc123",
        "private_key": pem,
        "client_email": "sa@test-project.iam.gserviceaccount.com",
        "client_id": "123456789",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


@pytest.fixture()
def _clean_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_CREDENTIALS_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)


def test_env_json_wins(_clean_env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", json.dumps(_fake_sa_info()))
    creds = vertex_credentials()
    assert creds is not None
    assert creds.service_account_email == "sa@test-project.iam.gserviceaccount.com"
    assert "https://www.googleapis.com/auth/cloud-platform" in creds.scopes


def test_key_file_fallback(_clean_env, monkeypatch, tmp_path):
    key = tmp_path / "sa.json"
    key.write_text(json.dumps(_fake_sa_info()), encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key))
    creds = vertex_credentials()
    assert creds is not None
    assert creds.service_account_email == "sa@test-project.iam.gserviceaccount.com"


def test_no_config_returns_none_for_adc(_clean_env):
    assert vertex_credentials() is None


def test_malformed_json_is_loud(_clean_env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", "{not json")
    with pytest.raises(json.JSONDecodeError):
        vertex_credentials()


def test_missing_key_file_falls_through(_clean_env, monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "C:/no/such/file.json")
    assert vertex_credentials() is None


def test_llm_vertex_client_uses_env_json(monkeypatch):
    """LLM(vertex) passes the env-JSON credentials to google-genai."""
    from studio_mind.config import LlmSettings
    from studio_mind.llm import LLM

    monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", json.dumps(_fake_sa_info()))
    llm = LLM(LlmSettings(provider="vertex", google_cloud_project="p",
                          google_cloud_location="us-central1"))
    captured = {}

    class _FakeGenaiClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import google.genai as genai_mod

    orig = genai_mod.Client
    genai_mod.Client = _FakeGenaiClient
    try:
        _ = llm.client  # noqa: F841 — forces the lazy client construction
    finally:
        genai_mod.Client = orig
    assert captured.get("vertexai") is True
    assert captured.get("project") == "p"
    assert captured.get("credentials") is not None
    assert captured["credentials"].service_account_email.endswith("@test-project.iam.gserviceaccount.com")


def test_api_entry_exposes_asgi_app():
    """api/index.py (the Vercel function) must export the FastAPI app and be
    importable from a bare path context."""
    import importlib.util

    path = pathlib.Path(__file__).resolve().parent.parent / "api" / "index.py"
    spec = importlib.util.spec_from_file_location("vercel_index", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from fastapi import FastAPI

    assert isinstance(mod.app, FastAPI)
    routes = {r.path for r in mod.app.routes}
    assert {"/", "/health", "/ask", "/examples"} <= routes


def test_serverless_flag_detected_from_env(monkeypatch):
    import importlib

    import studio_mind.mcp_transport as mt

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("SERVERLESS", raising=False)
    importlib.reload(mt)
    assert mt.SERVERLESS is True
    monkeypatch.delenv("VERCEL", raising=False)
    importlib.reload(mt)
    assert mt.SERVERLESS is False
