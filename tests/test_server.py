"""HTTP service tests (offline — the pipeline is monkeypatched, no network)."""

from __future__ import annotations

import json
import re

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


class TestWaitingUX:
    """M7-UX: /ask takes 45-90s cold — the judge page must look alive the whole
    time (spinner, ticking timer, stages, skeleton, cold-start note, timeout
    guard). Asserted against the served HTML since the logic is inline JS."""

    def test_button_spinner_and_label_swap(self, client):
        h = client[0].get("/").text
        assert 'id="btn-label"' in h                     # label element exists
        assert "'Thinking…'" in h                        # busy label, restored to 'Ask'
        assert "#btn .spin" in h and "@keyframes btnspin" in h
        assert "border-top-color:#f5e14b" in h           # spinner wears the brand color
        assert "btn.disabled = on" in h                  # disabled while busy, restored after

    def test_live_elapsed_timer(self, client):
        h = client[0].get("/").text
        assert "setInterval(step, 1000)" in h            # 1s tick
        assert 'id="st-sec"' in h                        # "Thinking… Ns" target element
        assert "performance.now() - t0" in h

    def test_stage_progression_copy_and_thresholds(self, client):
        h = client[0].get("/").text
        for s in ("Parsing your question", "Querying ClickHouse via official MCP server",
                  "Diagnosing audience patterns", "Writing your brief with SQL receipts"):
            assert s in h
        m = re.search(r"const STAGES = \[(.*?)\];", h, re.DOTALL)
        assert m, "STAGES table not found"
        for t in ("[0,", "[3,", "[10,", "[25,"):      # 0-3 / 3-10 / 10-25 / 25+ seconds
            assert t in m.group(1)
        assert "function stageFor" in h and "s >= t" in h

    def test_skeleton_answer_card(self, client):
        h = client[0].get("/").text
        assert "function skeleton" in h
        assert "sk-line" in h and "sk-pill" in h
        assert "@keyframes shimmer" in h                # shimmer animation
        assert 'aria-hidden="true"' in h                # skeleton is not announced

    def test_cold_start_expectation_note(self, client):
        h = client[0].get("/").text
        assert "const COLD_AT = 60" in h
        assert "ClickHouse Cloud cold resume" in h
        assert "Hang tight" in h

    def test_client_timeout_guard(self, client):
        h = client[0].get("/").text
        assert "FETCH_TIMEOUT_MS = 240000" in h         # ~240s give-up guard
        assert "AbortController" in h and "AbortError" in h
        assert "ctl.abort()" in h

    def test_completion_elapsed_pill_and_aria(self, client):
        h = client[0].get("/").text
        assert "Answered in " in h                      # statusline + sr announcement
        assert "pill-hot" in h and "<b>answered</b> in " in h   # elapsed pill in meta row
        assert 'id="main"' in h and "aria-busy" in h    # aria-busy on the main region
        assert 'aria-live="polite"' in h                # stage/result announcements

    def test_error_path_is_friendly(self, client):
        h = client[0].get("/").text
        assert "Something went wrong" in h
        assert "press Ask to retry" in h

    def test_no_external_assets(self, client):
        h = client[0].get("/").text
        assert "<script src" not in h                   # no CDN JS
        assert "<link" not in h                          # no external CSS/fonts
        assert "http://" not in h and "https://" not in h  # fully self-contained
