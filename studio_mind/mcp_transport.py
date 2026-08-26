"""Runtime ClickHouse access through the official mcp-clickhouse server.

This module is the hackathon-compliance core: every runtime query the agent
answers with goes through the official ClickHouse MCP server
(github.com/ClickHouse/mcp-clickhouse), spawned as a stdio subprocess and
driven over the MCP protocol. The direct clickhouse-connect client in
``studio_mind.ch`` exists for the 50M-row bulk seed and as an explicit
dev fallback — the demo, video and default config use THIS path.

Design:
  * one persistent MCP session per McpClient (background asyncio loop in a
    daemon thread — the subprocess stays warm across queries, so the
    millisecond timings in the trust panel reflect query cost, not process
    spawn cost);
  * tool surface is discovered at startup: newer official servers expose
    ``list_tables`` / ``select_query``; this pinned release exposes
    ``execute_query`` — both are spoken;
  * results are normalized to the same ``.result_rows`` / ``.column_names``
    shape clickhouse-connect produces, so the pipeline is transport-agnostic;
  * the connection is opened with CLICKHOUSE_READONLY=1 — read-only is
    enforced server-side too, not just by our sqlguard.

Trust-panel statistics (rows scanned, server-side latency) come from
``system.query_log`` — queried through the same MCP session, so the numbers
on screen are the numbers the MCP server itself reported.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

MCP_SERVER_MODULE = "clickhouse_mcp_server.server"  # official server, stdio entry


class McpTransportError(RuntimeError):
    pass


@dataclass
class QueryResult:
    """clickhouse-connect-compatible result over MCP rows."""

    column_names: list[str]
    result_rows: list[tuple]

    @property
    def rows_as_dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.column_names, row)) for row in self.result_rows]


_HTTP_TO_NATIVE = {8123: 9000, 8443: 9440}


def _normalize_url_to_native(url: str, secure_port: int = 9440, plain_port: int = 9000) -> tuple[str, int, bool]:
    """The official server speaks native TCP (clickhouse-driver), not HTTP.

    CLICKHOUSE_URL carries the HTTP port (8123/8443); remap to the native
    ports (9000/9440). Non-standard ports are passed through unchanged so
    custom deployments keep working.
    """
    m = re.match(r"(?:https?://)?([^:/]+)(?::(\d+))?", url or "")
    if not m:
        raise McpTransportError(f"cannot parse CLICKHOUSE_URL {url!r}")
    host = m.group(1)
    secure = url.startswith("https://")
    if m.group(2):
        port = _HTTP_TO_NATIVE.get(int(m.group(2)), int(m.group(2)))
    else:
        port = secure_port if secure else plain_port
    return host, port, secure


class _LoopThread:
    """A dedicated asyncio loop in a daemon thread for one MCP session."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True, name="mcp-session")
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=120)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


class McpClient:
    """Synchronous facade over one persistent stdio MCP session.

    Surface mirrors what the pipeline expects from a ClickHouse client::

        client = McpClient.from_settings(settings)
        res = client.query("SELECT 1 AS x")     # .result_rows, .column_names
        out = client.command("EXPLAIN ...")     # str
        stats = client.query_stats()            # trust panel row
    """

    # ``server_command`` lets tests point the client at a fake MCP server.
    def __init__(self, host: str, port: int, user: str, password: str,
                 database: str = "studio", readonly: bool = True,
                 server_command: list[str] | None = None):
        self.host, self.port, self.user = host, port, user
        self.password, self.database, self.readonly = password, database, readonly
        self._server_command = server_command
        self._loop = _LoopThread()
        self._session = None
        self._tool: str | None = None       # discovered: select_query | execute_query
        self._list_tool: str | None = None  # list_tables | None
        self._started_at = time.time()
        self._ready = threading.Event()
        self._init_error: BaseException | None = None
        self._worker = None
        self._connect()

    # -- construction ---------------------------------------------------------

    @classmethod
    def from_settings(cls, settings, server_command: list[str] | None = None) -> "McpClient":
        ch = settings.ch
        host, port, _secure = _normalize_url_to_native(ch.url)
        override = server_command or os.getenv("STUDIO_MIND_MCP_COMMAND")
        if override:
            override = json.loads(override) if override.startswith("[") else override.split()
        return cls(host=host, port=port, user=ch.user, password=ch.password,
                   database=ch.database, server_command=override)

    # -- MCP plumbing -----------------------------------------------------------

    def _connect(self) -> None:
        """Start the session inside a persistent worker task.

        stdio_client's context manager is task-scoped (anyio task group): if
        the coroutine that entered it returns, the subprocess is cancelled.
        So the worker task keeps the context open until close(), while other
        tasks on the same loop use the session for tool calls.
        """
        import asyncio

        self._worker = asyncio.run_coroutine_threadsafe(
            self._session_worker(), self._loop.loop)
        if not self._ready.wait(timeout=120):
            self._loop.stop()
            raise McpTransportError("mcp-clickhouse session did not become ready")
        if self._init_error is not None:
            self._loop.stop()
            raise McpTransportError(
                f"failed to start mcp-clickhouse session: {self._init_error}"
            ) from self._init_error

    async def _session_worker(self):
        import asyncio

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        command = self._server_command or [sys.executable, "-m", MCP_SERVER_MODULE]
        params = StdioServerParameters(
            command=command[0],
            args=command[1:],
            env={
                **os.environ,
                "CLICKHOUSE_HOST": self.host,
                "CLICKHOUSE_PORT": str(self.port),
                "CLICKHOUSE_USER": self.user,
                "CLICKHOUSE_PASSWORD": self.password,
                "CLICKHOUSE_DATABASE": self.database,
                "CLICKHOUSE_READONLY": "1" if self.readonly else "0",
            },
        )
        stop = asyncio.Event()
        self._signal_stop = stop
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    names = {t.name for t in tools}
                    select_tool = ("select_query" if "select_query" in names
                                   else "execute_query" if "execute_query" in names
                                   else None)
                    if select_tool is None:
                        raise McpTransportError(
                            f"mcp-clickhouse exposes no query tool (found: {sorted(names)})")
                    self._session = session
                    self._tool = select_tool
                    self._list_tool = "list_tables" if "list_tables" in names else None
                    self._ready.set()
                    await stop.wait()  # hold the stdio context open
        except BaseException as e:  # surfaced via _ready/_init_error
            self._init_error = e
            self._ready.set()

    def _call(self, tool: str, arguments: dict) -> Any:
        if self._session is None:
            raise McpTransportError("session not started")
        result = self._loop.run(self._session.call_tool(tool, arguments))
        if getattr(result, "isError", False):
            raise McpTransportError(f"tool {tool} failed: {result.content}")
        return result

    # -- client surface -----------------------------------------------------------

    def query(self, sql: str) -> QueryResult:
        """Run a SELECT and return rows normalized to the connect-client shape."""
        payload = self._extract(self._call(self._tool, {"query": sql}))
        return self._to_result(payload)

    def command(self, sql: str) -> Any:
        """Run EXPLAIN / PRAGMA-style statements; returns text."""
        payload = self._extract(self._call(self._tool, {"query": sql}))
        if isinstance(payload, str):
            return payload
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            first = payload[0]
            if len(first) == 1:
                return next(iter(first.values()))
            return json.dumps(payload, default=str)
        return str(payload)

    def list_tables(self) -> list[dict[str, Any]]:
        if self._list_tool:
            return self._extract(self._call(self._list_tool, {}))
        rows = self.query(
            "SELECT name, total_rows FROM system.tables "
            f"WHERE database = '{self.database}' ORDER BY name"
        ).rows_as_dicts
        return rows

    def ping(self) -> bool:
        try:
            self.query("SELECT 1 AS ok")
            return True
        except Exception:
            return False

    # -- trust panel -----------------------------------------------------------

    def query_stats(self, since: float | None = None) -> dict[str, Any] | None:
        """Latest QueryFinish from system.query_log (server-side truth for the
        trust panel: rows read, bytes, duration). Queried through MCP itself."""
        since_s = since if since is not None else self._started_at
        sql = (
            "SELECT read_rows, formatReadableSize(read_bytes) AS read_size, "
            "query_duration_ms, formatReadableSize(memory_usage) AS mem, "
            "uptime() AS server_uptime "
            "FROM system.query_log WHERE type = 'QueryFinish' "
            f"AND event_time >= FROM_UNIXTIME({int(since_s)}) "
            "AND query NOT LIKE '%system.query_log%' "
            "ORDER BY event_time_microseconds DESC LIMIT 1"
        )
        try:
            rows = self.query(sql).rows_as_dicts
            return rows[0] if rows else None
        except Exception as e:
            log.debug("query_stats unavailable: %s", e)
            return None

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _extract(result) -> Any:
        """MCP tool results arrive as content blocks; the official server
        returns one TextContent holding JSON."""
        parts = getattr(result, "content", None) or []
        texts = [p.text for p in parts if getattr(p, "type", "") == "text"]
        if not texts:
            raise McpTransportError("empty MCP result")
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]

    @staticmethod
    def _to_result(payload: Any) -> QueryResult:
        if isinstance(payload, dict) and "error" in payload:
            raise McpTransportError(str(payload["error"]))
        if isinstance(payload, str):
            raise McpTransportError(payload)
        if not isinstance(payload, list) or not payload:
            return QueryResult(column_names=[], result_rows=[])
        if not isinstance(payload[0], dict):
            raise McpTransportError(f"unexpected row shape: {type(payload[0])}")
        cols = list(payload[0].keys())
        rows = [tuple(row.get(c) for c in cols) for row in payload]
        return QueryResult(column_names=cols, result_rows=rows)

    def close(self) -> None:
        """Signal the worker to exit; its async-with blocks tear down the
        subprocess and session in order."""
        stop = getattr(self, "_signal_stop", None)
        if stop is not None and self._loop.loop.is_running():
            try:
                self._loop.run(stop.set())
            except Exception:
                pass
        if self._worker is not None:
            try:
                self._worker.result(timeout=15)
            except Exception:
                pass
        self._loop.stop()

    def __enter__(self) -> "McpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
