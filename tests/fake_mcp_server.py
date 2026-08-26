"""A fake official-mcp-clickhouse server for transport tests.

Speaks the REAL MCP protocol (stdio, FastMCP) with the same tool names and
JSON response shapes as the official server, but serves canned rows instead of
talking to ClickHouse. This exercises the full compliance path — subprocess
spawn, session initialize, tool discovery, tool call, JSON normalization —
without needing a database.

Run: python tests/fake_mcp_server.py   (stdio; driven by McpClient)
"""

from __future__ import annotations

import json
import sys

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-clickhouse")


def _rows_for(query: str):
    q = " ".join(query.lower().split())
    if q.startswith("explain"):
        return [{"explain": "Expression (Projection)\n  ReadFromMergeTree"}]
    if "select 1" in q:
        return [{"ok": 1}]
    if "system.query_log" in q:
        return [{
            "read_rows": 1_234_567,
            "read_size": "45.62 MiB",
            "query_duration_ms": 318,
            "mem": "128.00 MiB",
            "server_uptime": 86_400,
        }]
    if "system.tables" in q:
        return [
            {"name": "episodes", "total_rows": 4500},
            {"name": "titles", "total_rows": 180},
            {"name": "users", "total_rows": 1_200_000},
            {"name": "viewing_events", "total_rows": 50_000_000},
        ]
    if q.startswith("explain"):
        return [{"explain": "Expression (Projection)\n  ReadFromMergeTree"}]
    if "fail" in q:
        return {"error": "fake server: simulated failure"}
    return [{"n": 42}]


@mcp.tool()
def execute_query(query: str) -> str:
    """Execute a read-only SQL query (fake backend)."""
    return json.dumps(_rows_for(query))


@mcp.tool()
def list_tables() -> str:
    """List tables in the database (fake backend)."""
    return json.dumps(_rows_for("SELECT * FROM system.tables"))


if __name__ == "__main__":
    sys.exit(mcp.run())
