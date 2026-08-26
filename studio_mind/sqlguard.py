"""SQL guardrail: only validated read-only analytics reach ClickHouse."""

from __future__ import annotations

import re

FORBIDDEN = re.compile(
    r"\b(insert|alter|create|drop|truncate|delete|rename|grant|revoke|"
    r"kill|optimize|attach|detach|attach|freeze|backup|restore)\b",
    re.IGNORECASE,
)
# Note: system.* reads (system.tables, system.query_log, ...) are ALLOWED —
# schema introspection and the trust panel need them. Write-shaped system
# commands (SYSTEM MERGES, KILL QUERY, SET ROLE) can never reach execution:
# validate_sql only passes statements whose first token is SELECT/WITH.
MAX_LIMIT = 10_000


class SQLRejected(ValueError):
    pass


def validate_sql(sql: str, max_limit: int = MAX_LIMIT) -> str:
    """Validate an LLM- or pattern-authored query. Returns cleaned SQL.

    Rules:
      * exactly one statement, must start with SELECT or WITH
      * no write/DDL/admin keywords anywhere
      * no comments (avoid trivial obfuscation)
      * result capped: LIMIT injected when absent
    """
    s = sql.strip().rstrip(";").strip()
    # strip line comments
    s = re.sub(r"--[^\n]*", "", s)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = " ".join(s.split())

    if not s:
        raise SQLRejected("empty query")
    if ";" in s:
        raise SQLRejected("multiple statements are not allowed")
    first = s.split(None, 1)[0].upper()
    if first not in ("SELECT", "WITH"):
        raise SQLRejected(f"only SELECT/WITH queries allowed, got {first!r}")
    m = FORBIDDEN.search(s)
    if m:
        raise SQLRejected(f"forbidden keyword: {m.group(0)!r}")

    has_limit = re.search(r"\blimit\s+\d+\s*$", s, re.IGNORECASE) or re.search(
        r"\blimit\s+\d+\s*,", s, re.IGNORECASE
    )
    if not has_limit:
        s = f"{s} LIMIT {max_limit}"
    else:
        # clamp an existing trailing LIMIT that is too large
        def _clamp(mm: re.Match) -> str:
            n = int(mm.group(1))
            return f"LIMIT {min(n, max_limit)}"

        s = re.sub(r"LIMIT\s+(\d+)\s*$", _clamp, s, flags=re.IGNORECASE)
    return s
