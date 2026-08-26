"""Transport selection + run_query trust-panel wiring (no live ClickHouse)."""

from __future__ import annotations

import pytest

from studio_mind import ch
from studio_mind.config import load_settings
from studio_mind.evidence import EvidenceRegistry


class FakeMcpLikeClient:
    """Duck-typed stand-in for McpClient (query + query_stats + close)."""

    module_marker = True
    __class_module__ = "studio_mind.mcp_transport"  # not used; module attr below

    def __init__(self):
        self.__class__.__module__ = "studio_mind.mcp_transport"  # for close_client
        self.closed = False

    def query(self, sql):
        class R:
            column_names = ["n"]
            result_rows = [(7,)]
        return R()

    def command(self, sql):
        return "EXPLAIN ok"

    def query_stats(self):
        return {"read_rows": 50_000_000, "read_size": "180.00 MiB",
                "query_duration_ms": 245}

    def close(self):
        self.closed = True


class FakeConnectClient:
    """clickhouse-connect-shaped client (no query_stats → no server stats)."""

    def query(self, sql):
        class R:
            column_names = ["n"]
            result_rows = [(7,)]
        return R()

    def command(self, sql):
        return "EXPLAIN ok"


def test_default_transport_is_mcp():
    s = load_settings(env_file=None)
    assert s.ch.transport == "mcp"


def test_get_client_selects_by_transport(monkeypatch):
    s = load_settings(env_file=None).with_overrides(ch__transport="http")
    # http path calls clickhouse_connect.get_client — stub it
    import clickhouse_connect

    monkeypatch.setattr(clickhouse_connect, "get_client", lambda **kw: object())
    client = ch.get_client(s)
    assert client.__class__.__module__ != "studio_mind.mcp_transport"

    s_mcp = load_settings(env_file=None).with_overrides(ch__transport="mcp")
    # point at the fake MCP server so no ClickHouse is needed
    import sys
    from pathlib import Path

    fake = str(Path(__file__).with_name("fake_mcp_server.py"))
    monkeypatch.setenv("STUDIO_MIND_MCP_COMMAND", f"{sys.executable} {fake}")
    client2 = ch.get_client(s_mcp)
    try:
        assert client2.ping()
    finally:
        client2.close()


def test_run_query_records_trust_stats():
    registry = EvidenceRegistry()
    fake = FakeMcpLikeClient()
    ev = ch.run_query(fake, registry, "test purpose", "SELECT 1 AS n")
    assert ev.ok
    assert ev.read_rows == 50_000_000
    assert ev.read_size == "180.00 MiB"
    assert ev.server_ms == 245.0
    assert "rows scanned" in ev.trust_line()
    assert "245" in ev.trust_line() or "ms server" in ev.trust_line()


def test_run_query_without_stats_still_works():
    registry = EvidenceRegistry()
    ev = ch.run_query(FakeConnectClient(), registry, "p", "SELECT 1 AS n")
    assert ev.ok
    assert ev.read_rows is None
    assert "ms wall" in ev.trust_line()


def test_run_query_rejects_writes():
    registry = EvidenceRegistry()
    ev = ch.run_query(FakeConnectClient(), registry, "p", "DROP TABLE titles")
    assert not ev.ok
    assert "rejected" in ev.error


def test_close_client_closes_mcp_only():
    fake = FakeMcpLikeClient()
    ch.close_client(fake)
    assert fake.closed
    plain = object()
    ch.close_client(plain)  # no-op, no crash
