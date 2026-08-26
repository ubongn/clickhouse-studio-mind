"""MCP transport tests — the compliance path, exercised end-to-end over real
stdio JSON-RPC against a fake that mimics the OFFICIAL mcp-clickhouse server
(PyPI mcp-clickhouse). No ClickHouse required."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from studio_mind.mcp_transport import (
    McpClient,
    McpTransportError,
    _normalize_url,
)

FAKE = [sys.executable, str(Path(__file__).with_name("fake_mcp_server.py"))]


@pytest.fixture(scope="module")
def client():
    c = McpClient(host="localhost", port=8123, user="default", password="",
                  database="studio", server_command=FAKE)
    yield c
    c.close()


def test_url_http_port_passthrough():
    # the official server speaks the HTTP interface: ports pass through as-is
    assert _normalize_url("http://localhost:8123") == ("localhost", 8123, False)
    assert _normalize_url("https://abc.aws.clickhouse.cloud:8443") == \
        ("abc.aws.clickhouse.cloud", 8443, True)
    # no port → HTTP default 8123, HTTPS default 8443
    assert _normalize_url("http://box") == ("box", 8123, False)
    assert _normalize_url("https://box") == ("box", 8443, True)
    # custom ports pass through untouched
    assert _normalize_url("http://box:9100") == ("box", 9100, False)


def test_query_roundtrip_and_shapes(client):
    res = client.query("SELECT 1 AS ok")
    assert res.column_names == ["ok"]
    assert res.result_rows == [(1,)]
    assert res.rows_as_dicts == [{"ok": 1}]


def test_official_tool_discovered(client):
    # official server surface: run_query + list_tables
    assert client._tool == "run_query"
    assert client._list_tool == "list_tables"


def test_command_normalizes_explain_output(client):
    out = client.command("EXPLAIN SELECT 1")
    assert isinstance(out, str)
    assert "ReadFromMergeTree" in out


def test_list_tables_official_shape(client):
    tables = client.list_tables()
    names = {t["name"] for t in tables}
    assert {"viewing_events", "titles", "users", "episodes"} <= names


def test_trust_panel_stats_via_query_log(client):
    client.query("SELECT uniqExact(user_id) FROM viewing_events")  # produce a "query"
    stats = client.query_stats()
    assert stats is not None
    assert stats["read_rows"] == 1_234_567
    assert stats["query_duration_ms"] == 318


def test_ping(client):
    assert client.ping() is True


def test_error_payload_raises(client):
    with pytest.raises(McpTransportError):
        client.query("SELECT fail NOW")


def test_bad_command_fails_fast():
    with pytest.raises(McpTransportError):
        McpClient(host="x", port=1, user="u", password="", database="d",
                  server_command=[sys.executable, "-c", "import sys; sys.exit(3)"])


def test_settings_factory_reads_env_override(monkeypatch):
    from studio_mind.config import load_settings

    monkeypatch.setenv("STUDIO_MIND_MCP_COMMAND", f"{sys.executable} {Path(__file__).with_name('fake_mcp_server.py')}")
    s = load_settings(env_file=None)
    c = McpClient.from_settings(s)
    try:
        assert c.ping()
    finally:
        c.close()
