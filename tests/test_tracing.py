"""Tracing tests — span trees, auto-nesting, Langfuse-shaped JSON."""

from __future__ import annotations

import json

from studio_mind import tracing
from studio_mind.evidence import EvidenceRegistry
from studio_mind.ch import run_query
from tests.test_transport_selection import FakeMcpLikeClient


def test_span_tree_nesting_and_latency():
    c = tracing.TraceCollector("test-run")
    with c.span("stage · query"):
        with c.tool_span("mcp-clickhouse · select_query", "SELECT 1") as t1:
            t1.metadata.update({"read_rows": 50_000_000})
        with c.tool_span("mcp-clickhouse · select_query", "SELECT 2"):
            pass
    tree = c.tree()
    assert "TRACE test-run" in tree
    assert "stage · query" in tree
    assert tree.count("mcp-clickhouse · select_query") == 2
    assert "50,000,000 rows scanned" in tree
    assert "ms" in tree


def test_latency_recorded_and_positive():
    c = tracing.TraceCollector("x")
    with c.span("outer"):
        with c.span("inner"):
            pass
    obs = {o.name: o for o in c.observations}
    assert obs["inner"].latency_ms is not None and obs["inner"].latency_ms >= 0
    assert obs["outer"].latency_ms >= obs["inner"].latency_ms
    assert obs["inner"].parent_id == obs["outer"].id
    assert obs["outer"].parent_id is None


def test_json_is_langfuse_shaped():
    c = tracing.TraceCollector("x", metadata={"transport": "mcp"})
    with c.llm_span("gemini · structured", "gemini-2.5-flash", "prompt") as g:
        g.usage = {"input": 100, "output": 50, "total": 150}
    d = c.to_dict()
    assert set(d) == {"trace", "observations"}
    assert d["trace"]["id"].startswith("tr-")
    gen = d["observations"][0]
    assert gen["type"] == "generation"
    assert gen["usage"]["total"] == 150
    assert gen["latency_ms"] is not None
    json.dumps(d)  # fully serializable


def test_error_observation_marks_level():
    c = tracing.TraceCollector("x")
    try:
        with c.span("boom"):
            raise ValueError("nope")
    except ValueError:
        pass
    obs = c.observations[0]
    assert obs.level == "ERROR"
    assert "nope" in str(obs.output)
    assert c.totals()["errors"] == 1


def test_run_query_emits_tool_span_into_active_trace():
    c = tracing.TraceCollector("run")
    token = tracing.set_active_collector(c)
    try:
        registry = EvidenceRegistry()
        with c.span("stage · query"):
            ev = run_query(FakeMcpLikeClient(), registry, "p", "SELECT 1 AS n")
        tools = [o for o in c.observations if o.type == "tool"]
        assert len(tools) == 1
        assert tools[0].metadata["evidence_id"] == ev.id
        assert tools[0].metadata["read_rows"] == 50_000_000
        assert tools[0].metadata["wall_ms"] >= 0
        # nested under the stage span
        stage = [o for o in c.observations if o.name == "stage · query"][0]
        assert tools[0].parent_id == stage.id
    finally:
        tracing.reset_active_collector(token)


def test_no_active_collector_is_safe():
    assert tracing.active_collector() is None
    registry = EvidenceRegistry()
    ev = run_query(FakeMcpLikeClient(), registry, "p", "SELECT 1 AS n")
    assert ev.ok  # tracing absence never breaks queries


def test_totals_and_clipping():
    c = tracing.TraceCollector("x")
    with c.span("s", input_="x" * 5000):
        pass
    assert c.observations[0].input.endswith("chars)")
    t = c.totals()
    assert t["observations"] == 1 and t["errors"] == 0
