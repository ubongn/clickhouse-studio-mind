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


def _load_entry():
    """Import api/index.py (the Vercel function) from a bare path context."""
    import importlib.util

    path = pathlib.Path(__file__).resolve().parent.parent / "api" / "index.py"
    spec = importlib.util.spec_from_file_location("vercel_index", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_api_entry_exposes_asgi_app():
    """api/index.py (the Vercel function) must export an ASGI callable
    wrapping the FastAPI app (whose import path is unchanged)."""
    from fastapi import FastAPI

    mod = _load_entry()
    assert callable(mod.app)
    fastapi_app = getattr(mod.app, "app", mod.app)
    assert isinstance(fastapi_app, FastAPI)
    routes = {r.path for r in fastapi_app.routes}
    assert {"/", "/health", "/ask", "/examples"} <= routes


# --- Vercel rewrite-path middleware (__vc_path) ----------------------------
# New Vercel build pipeline (CLI 59.x / UI-import) hands the app the rewritten
# destination path (/api/index) for every request; vercel.json forwards the
# client's original path as ?__vc_path=... and the entry's middleware restores
# it. These tests simulate both pipeline shapes, fully offline.


def _http_scope(path, query_string=b""):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query_string,
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }


def _drive(asgi_app, scope):
    """Run one request through an ASGI app; return (status, headers, body)."""
    import asyncio

    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(asgi_app(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    status = start["status"]
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in start["headers"]}
    body = b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )
    return status, headers, body


def test_middleware_restores_path_and_strips_marker():
    """http scope with ?__vc_path=/health -> app sees /health, marker gone,
    other query params kept, and the caller's scope dict is not mutated."""
    mod = _load_entry()
    seen = {}

    async def spy(scope, receive, send):
        seen.update(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapper = mod.VercelRewritePathASGI(spy)
    scope = _http_scope("/api/index", b"__vc_path=/health&deep=1")
    _drive(wrapper, scope)
    assert seen["path"] == "/health"
    assert seen["raw_path"] == b"/health"
    assert seen["query_string"] == b"deep=1"
    assert scope["path"] == "/api/index"  # original scope untouched
    assert scope["query_string"] == b"__vc_path=/health&deep=1"


def test_middleware_new_pipeline_health_reaches_route():
    """New-pipeline shape: request /api/index?__vc_path=/health -> 200 health
    JSON (shallow, offline), not FastAPI's 404."""
    mod = _load_entry()
    status, headers, body = _drive(
        mod.app, _http_scope("/api/index", b"__vc_path=/health")
    )
    assert status == 200
    out = json.loads(body)
    assert out["status"] == "ok"
    assert out["service"] == "studio-mind"


def test_middleware_new_pipeline_root_and_examples():
    """Blank marker (client asked for /) maps to the root page; /examples
    routes too."""
    mod = _load_entry()
    status, headers, body = _drive(
        mod.app, _http_scope("/api/index", b"__vc_path=")
    )
    assert status == 200
    assert "text/html" in headers["content-type"]

    status, _, body = _drive(
        mod.app, _http_scope("/api/index", b"__vc_path=/examples")
    )
    assert status == 200
    assert isinstance(json.loads(body), (list, dict))


def test_middleware_old_pipeline_passthrough():
    """No marker (old pipeline / uvicorn / Cloud Run): scope untouched."""
    mod = _load_entry()
    status, _, body = _drive(mod.app, _http_scope("/health"))
    assert status == 200
    assert json.loads(body)["status"] == "ok"

    status, _, _ = _drive(mod.app, _http_scope("/api/index"))
    assert status == 404  # genuinely unknown path stays 404


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
