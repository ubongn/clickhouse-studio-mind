"""HTTP service tests (offline — the pipeline is monkeypatched, no network)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from studio_mind import server
from studio_mind.pipeline.run import RunResult


def make_result(question="Which genres keep viewers past episode 3 in EMEA?") -> RunResult:
    registry = json.dumps([{
        "id": "Q1", "purpose": "retention by genre",
        "sql": "SELECT genre, avg(retention) FROM studio.events GROUP BY genre",
        "columns": ["genre", "avg(retention)"], "rows": [["drama", 0.82]],
    }])
    return RunResult(
        question=question, intent={"kind": "segment", "dimension": "genre"},
        brief="Drama retains best.", primary_ids=["Q1"],
        timings={"query_ms": 120.0, "total_ms": 340.0},
        registry_json=registry, trace_json="{}",
        trace_tree="ask\n └─ pipeline", llm_used=True,
    )


@pytest.fixture
def client(monkeypatch):
    captured = {}

    def fake_run(question, settings=None, use_llm=True):
        captured["question"] = question
        captured["use_llm"] = use_llm
        return make_result(question)

    monkeypatch.setattr(server, "run_pipeline", fake_run)
    app = server.create_app()
    # settings are read per-request via get_settings(); defaults are fine offline
    with TestClient(app) as c:
        yield c, captured


class TestHealth:
    def test_health_shape(self, client):
        c, _ = client
        r = c.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["service"] == "studio-mind"
        assert {"transport", "database", "provider", "model"} <= set(body)

    def test_health_deep_failure_is_degraded_not_500(self, client, monkeypatch):
        c, _ = client

        def boom(settings=None):
            raise RuntimeError("warehouse unreachable")

        monkeypatch.setattr("studio_mind.ch.get_client", boom)
        r = c.get("/health", params={"deep": "1"})
        assert r.status_code == 200
        assert r.json()["status"] == "degraded"
        assert "warehouse unreachable" in r.json()["clickhouse"]["error"]


class TestAsk:
    def test_post_ask(self, client):
        c, captured = client
        r = c.post("/ask", json={"question": "Do partnership users churn faster?"})
        assert r.status_code == 200
        body = r.json()
        assert captured["question"] == "Do partnership users churn faster?"
        assert body["brief"].startswith("Drama retains")
        assert body["evidence"][0]["id"] == "Q1"
        assert body["evidence"][0]["sql"].startswith("SELECT genre")
        assert body["timings"]["query_ms"] == 120.0
        assert body["llm_used"] is True
        assert body["trace_tree"].startswith("ask")

    def test_get_ask_query_param(self, client):
        c, captured = client
        r = c.get("/ask", params={"q": "Why did Nightfall lose audience?"})
        assert r.status_code == 200
        assert captured["question"] == "Why did Nightfall lose audience?"
        assert r.json()["intent"]["dimension"] == "genre"

    def test_empty_question_400(self, client):
        c, _ = client
        assert c.post("/ask", json={"question": "   "}).status_code == 400

    def test_pipeline_error_502(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(
            server, "run_pipeline",
            lambda q, settings=None, use_llm=True: (_ for _ in ()).throw(RuntimeError("boom")))
        r = c.post("/ask", json={"question": "anything"})
        assert r.status_code == 502
        assert "boom" in r.json()["detail"]


class TestPages:
    def test_index_html(self, client):
        c, _ = client
        r = c.get("/")
        assert r.status_code == 200
        assert "ClickHouse Studio Mind" in r.text
        # house style: light theme, inline SVG icons, no emoji anywhere
        assert 'color-scheme: light' in r.text
        assert "<svg" in r.text
        assert "mcp-clickhouse" in r.text and "Vertex" in r.text

    def test_examples(self, client):
        c, _ = client
        r = c.get("/examples")
        assert r.status_code == 200
        ex = r.json()["examples"]
        assert len(ex) == 3
        assert all(isinstance(q, str) for q in ex)
