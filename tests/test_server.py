"""HTTP service tests (offline — the pipeline is monkeypatched, no network)."""

from __future__ import annotations

import json
import re
import urllib.parse

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
        # <link> is allowed only as an inline data: URI (the SVG favicon) —
        # never a fetch from another origin.
        for attrs in re.findall(r"<link\b([^>]*)>", h):
            href = re.search(r"href\s*=\s*[\"']([^\"']*)[\"']", attrs)
            assert href and href.group(1).startswith("data:"), f"external <link>: {attrs}"
        # fully self-contained: no off-origin URLs outside data: URIs
        # (the favicon's SVG namespace lives inside its data: URI)
        stripped = re.sub(r"data:[^\"]*", "", h)
        assert "http://" not in stripped and "https://" not in stripped

    def test_favicon_inline_svg(self, client):
        """Tab icon matches the header mark: yellow rounded square + dark glyph."""
        h = client[0].get("/").text
        m = re.search(r"<link rel=\"icon\" type=\"image/svg\+xml\" href=\"data:image/svg\+xml,([^\"]+)\"", h)
        assert m, "inline SVG favicon link missing"
        icon = m.group(1)
        assert icon.startswith("%3Csvg") and icon.endswith("%3E")      # URL-encoded SVG
        assert "%23f5e14b" in icon and "%231c2430" in icon             # brand colors
        assert "http" in urllib.parse.unquote(icon)                    # xmlns present


class TestMorning:
    """/morning — the proactive 7am brief, same trust machinery as /ask."""

    @staticmethod
    def _fake_morning(client_obj, registry, database, date=None):
        from studio_mind.morning import MorningResult

        registry.add(purpose="daily metrics (9-day series)",
                     sql=f"SELECT toDate(event_time) AS day FROM {database}.viewing_events",
                     columns=["day", "events"], rows=[["2026-05-21", 44900]],
                     elapsed_ms=180.0, read_rows=50_000_000,
                     read_size="180.00 MiB", server_ms=245.0)
        return MorningResult(
            date=date or "2026-05-21",
            metrics=[{"key": "rebuffer_per_event", "label": "Rebuffer s/event",
                      "good": "lower", "value": 2.1, "baseline": 0.224,
                      "delta": 1.876, "pct": 836.6, "z": 15.3}],
            watchlist=[{"level": "high", "metric": "Rebuffer s/event", "z": 15.3,
                        "pct": 836.6,
                        "detail": "Rebuffer s/event 2.1 vs baseline 0.224 (+836.6%, z=+15.30)"}],
            attribution=[{"device": "mobile", "region": "NA",
                          "rebuffer_per_event": 5.9, "events": 12400,
                          "detail": "NA · mobile: 5.90s rebuffer/event over 12,400 events"}],
            churn={"today": 214, "baseline_per_day": 96.0},
            brief_md="# Morning Brief — 2026-05-21 · Nimbus+ Studio Ops\n\nship it",
            registry=registry,
        )

    def test_morning_endpoint_shape(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(server, "build_morning", self._fake_morning)
        monkeypatch.setattr("studio_mind.ch.get_client", lambda s=None: object())
        monkeypatch.setattr("studio_mind.ch.close_client", lambda c: None)
        r = c.get("/morning")
        assert r.status_code == 200
        body = r.json()
        assert body["date"] == "2026-05-21"
        assert body["watchlist"][0]["metric"] == "Rebuffer s/event"
        assert body["evidence"][0]["read_rows"] == 50_000_000
        assert "total_ms" in body["timings"]
        assert "morning" in body["trace_tree"].lower() or "TRACE" in body["trace_tree"]

    def test_morning_bad_date_is_422(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr("studio_mind.ch.get_client", lambda s=None: object())
        monkeypatch.setattr("studio_mind.ch.close_client", lambda c: None)

        def boom(*a, **k):
            raise ValueError("date must be YYYY-MM-DD")

        monkeypatch.setattr(server, "build_morning", boom)
        r = c.get("/morning", params={"date": "yesterday"})
        assert r.status_code == 422
        assert "YYYY-MM-DD" in r.json()["detail"]

    def test_morning_pinned_date_passes_through(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr("studio_mind.ch.get_client", lambda s=None: object())
        monkeypatch.setattr("studio_mind.ch.close_client", lambda c: None)
        seen = {}

        def fake(client_obj, registry, database, date=None):
            seen["date"] = date
            return self._fake_morning(client_obj, registry, database, date)

        monkeypatch.setattr(server, "build_morning", fake)
        r = c.get("/morning", params={"date": "2026-05-21"})
        assert r.status_code == 200
        assert seen["date"] == "2026-05-21"
        assert r.json()["date"] == "2026-05-21"


class TestMorningUX:
    """Page affordance: the morning-brief chip, renderer, and trust pill."""

    def test_morning_chip_on_page(self, client):
        h = client[0].get("/").text
        assert 'id="mbtn"' in h and "morningBrief()" in h
        assert "what changed overnight" in h
        assert "chip-am" in h                                   # dashed = secondary action

    def test_morning_renderer_and_status_copy(self, client):
        h = client[0].get("/").text
        assert "function renderMorning" in h
        assert "Morning brief ready in " in h
        assert "3 SQL receipts via the official MCP server" in h
        assert "MORNING_TIMEOUT_MS = 120000" in h               # shorter guard than /ask

    def test_trust_pill_on_every_evidence_card(self, client):
        h = client[0].get("/").text
        assert "function trustPill" in h
        assert "rows scanned" in h                              # scan receipt copy
        assert "ms wall" in h and "ms server" in h
        # both renderers apply it (the def itself doesn't count)
        assert h.count("+ trustPill(e)") == 2

    def test_watchlist_badges_render(self, client):
        h = client[0].get("/").text
        assert "Watchlist" in h
        assert "pill-hot" in h and "z " in h

