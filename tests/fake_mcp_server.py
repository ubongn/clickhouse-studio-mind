"""A fake official mcp-clickhouse server for transport tests.

Speaks the REAL MCP protocol (stdio, FastMCP) with the same tool names,
arguments and JSON response shapes as the OFFICIAL server
(github.com/ClickHouse/mcp-clickhouse, PyPI mcp-clickhouse):

  * ``run_query(query: str)`` → ``{"columns": [...], "rows": [[...], ...]}``
  * ``list_tables(database: str)`` → ``{"tables": [...], "next_page_token": null,
    "total_tables": N}``

but serves canned rows instead of talking to ClickHouse. This exercises the
full compliance path — subprocess spawn, session initialize, tool discovery,
tool call, JSON envelope normalization — without needing a database.

Run: python tests/fake_mcp_server.py   (stdio; driven by McpClient)
"""

from __future__ import annotations

import json
import sys

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-mcp-clickhouse")


def _result_for(query: str):
    """Official run_query envelope: {"columns": [...], "rows": [...]}."""
    q = " ".join(query.lower().split())
    if q.startswith("explain"):
        return {"columns": ["explain"],
                "rows": [["Expression (Projection)"], ["  ReadFromMergeTree"]]}
    if "select 1" in q:
        return {"columns": ["ok"], "rows": [[1]]}
    if "system.query_log" in q:
        return {"columns": ["read_rows", "read_size", "query_duration_ms", "mem",
                            "server_uptime"],
                "rows": [[1_234_567, "45.62 MiB", 318, "128.00 MiB", 86_400]]}
    if "system.tables" in q:
        return {"columns": ["name", "total_rows"],
                "rows": [["episodes", 4500], ["titles", 180],
                         ["users", 1_200_000], ["viewing_events", 50_000_000]]}
    if "fail" in q:
        return {"error": "fake server: simulated failure"}
    return {"columns": ["n"], "rows": [[42]]}


@mcp.tool()
def run_query(query: str) -> str:
    """Execute a read-only SQL query (fake backend)."""
    return json.dumps(_result_for(query))


@mcp.tool()
def list_tables(database: str, like: str | None = None,
                not_like: str | None = None, page_token: str | None = None,
                page_size: int = 50, include_detailed_columns: bool = True) -> str:
    """List tables in a database (fake backend, official response shape)."""
    return json.dumps({
        "tables": [
            {"name": "episodes", "total_rows": 4500},
            {"name": "titles", "total_rows": 180},
            {"name": "users", "total_rows": 1_200_000},
            {"name": "viewing_events", "total_rows": 50_000_000},
        ],
        "next_page_token": None,
        "total_tables": 4,
    })


if __name__ == "__main__":
    sys.exit(mcp.run())
