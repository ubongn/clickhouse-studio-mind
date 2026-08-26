"""ClickHouse access layer — transport selector + shared query runner.

Two transports, one decision: runtime analytics default to the OFFICIAL
mcp-clickhouse server (spawned stdio, driven over MCP — the connection the
hackathon checks). ``STUDIO_MIND_TRANSPORT=http`` switches to a direct
clickhouse-connect client for dev without an MCP server, and the 50M-row
loader uses clickhouse-connect regardless (bulk insert path, not runtime).

Every analytics query, on either transport:
  1. passes sqlguard validation (read-only, capped)
  2. is EXPLAINed (shape captured for the evidence record)
  3. executes with a time guard
  4. is recorded in the EvidenceRegistry with its full result + server-side
     scan stats when the MCP transport provides them (trust panel numbers)
"""

from __future__ import annotations

import time

import clickhouse_connect

from .config import Settings, get_settings
from .evidence import EvidenceRegistry
from .sqlguard import SQLRejected, validate_sql


def get_client(settings: Settings | None = None):
    """Return the runtime client for the configured transport.

    transport=mcp  → official mcp-clickhouse server over stdio (default, the
                     compliant demo path — see mcp_transport.McpClient)
    transport=http → direct clickhouse-connect client (dev fallback)
    """
    s = settings or get_settings()
    if s.ch.transport == "mcp":
        from .mcp_transport import McpClient

        return McpClient.from_settings(s)
    return clickhouse_connect.get_client(
        url=s.ch.url,
        username=s.ch.user,
        password=s.ch.password,
        database=s.ch.database,
        settings={"max_execution_time": s.pipeline.query_timeout_s},
    )


def close_client(client) -> None:
    """MCP clients own a subprocess; clickhouse-connect clients don't."""
    close = getattr(client, "close", None)
    if close and client.__class__.__module__.startswith("studio_mind"):
        try:
            close()
        except Exception:
            pass


def schema_context(client, database: str | None = None) -> str:
    """Compact schema brief for LLM prompts: tables, columns, types, sizes."""
    db = database or get_settings().ch.database
    tables = client.query(
        f"SELECT name, total_rows, formatReadableSize(total_bytes) "
        f"FROM system.tables WHERE database = {db!r} ORDER BY name"
    )
    cols = client.query(
        f"SELECT table, name, type, comment FROM system.columns "
        f"WHERE database = {db!r} ORDER BY table, position"
    )
    by_table: dict[str, list[str]] = {}
    for table, name, ctype, comment in cols.result_rows:
        line = f"{name} {ctype}"
        if comment:
            line += f" -- {comment}"
        by_table.setdefault(table, []).append(line)
    out = [f"Database {db} — tables:"]
    for name, rows, size in tables.result_rows:
        out.append(f"\nTABLE {name}  (~{rows:,} rows, {size})")
        out.extend("  " + c for c in by_table.get(name, []))
    return "\n".join(out)


def title_glossary(client, database: str | None = None, limit: int = 200) -> str:
    """Title names + ids so the LLM binds entity names correctly."""
    db = database or get_settings().ch.database
    res = client.query(
        f"SELECT title_id, title_name, genre, cadence, quality_arc, is_original "
        f"FROM {db}.titles ORDER BY popularity DESC LIMIT {limit}"
    )
    return "\n".join(
        f"{tid}: {name} ({genre}, {cadence}, arc={arc}{' ,original' if orig else ''})"
        for tid, name, genre, cadence, arc, orig in res.result_rows
    )


def run_query(client, registry: EvidenceRegistry, purpose: str, sql: str,
              settings: Settings | None = None) -> "object":
    """Validate → EXPLAIN → execute → record. Returns the Evidence object."""
    s = settings or get_settings()
    try:
        safe_sql = validate_sql(sql, max_limit=s.pipeline.max_result_rows)
    except SQLRejected as e:
        return registry.add(purpose=purpose, sql=sql, error=f"rejected: {e}")

    plan = None
    try:
        plan_res = client.command(f"EXPLAIN actions = 0 {safe_sql}")
        plan = str(plan_res)[:4000]
    except Exception:
        pass  # EXPLAIN is a transparency aid, not a gate

    t0 = time.perf_counter()
    from . import tracing

    span_ctx = tracing.maybe_tool_span(
        f"mcp-clickhouse · {getattr(client, '_tool', 'select_query')}", safe_sql)
    with span_ctx as span:
        try:
            res = client.query(safe_sql)
            elapsed = (time.perf_counter() - t0) * 1000
            # trust panel: server-side scan stats through the same transport
            stats = None
            query_stats = getattr(client, "query_stats", None)
            if query_stats is not None:
                try:
                    stats = query_stats()
                except Exception:
                    stats = None
            ev = registry.add(
                purpose=purpose, sql=safe_sql,
                columns=list(res.column_names),
                rows=[list(r) for r in res.result_rows],
                elapsed_ms=elapsed, plan=plan,
                read_rows=(stats or {}).get("read_rows"),
                read_size=(stats or {}).get("read_size"),
                server_ms=float((stats or {}).get("query_duration_ms") or 0.0) or None,
            )
            span.metadata.update({
                "evidence_id": ev.id,
                "wall_ms": round(elapsed, 1),
                "server_ms": ev.server_ms,
                "read_rows": ev.read_rows,
                "read_size": ev.read_size,
                "rows_returned": ev.row_count,
                "plan_captured": plan is not None,
            })
            return ev
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            span.metadata.update({"wall_ms": round(elapsed, 1)})
            return registry.add(purpose=purpose, sql=safe_sql, elapsed_ms=elapsed,
                                plan=plan, error=str(e)[:500])
