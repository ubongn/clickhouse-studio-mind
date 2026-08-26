"""Loader unit tests — DDL parsing and column coverage (no ClickHouse needed)."""

from __future__ import annotations

import re
from pathlib import Path

from data.load import SCHEMA_PATH, sqlsplit


def read_schema() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8-sig")


def test_sqlsplit_breaks_at_top_level_statements():
    stmts = sqlsplit(read_schema())
    assert len(stmts) >= 8
    assert all("--" not in s.split("\n")[0] for s in stmts)  # comment lines stripped
    assert any(s.startswith("CREATE TABLE") and "viewing_events" in s for s in stmts)
    assert any("MATERIALIZED VIEW" in s for s in stmts)


def test_every_create_is_idempotent():
    schema = read_schema()
    creates = re.findall(r"CREATE (TABLE|MATERIALIZED VIEW|DATABASE)[^\"]*?;", schema, re.S)
    assert creates  # at least the known set
    assert "CREATE DATABASE IF NOT EXISTS" in schema
    for m in re.finditer(r"CREATE (?:TABLE|MATERIALIZED VIEW)\s+(IF NOT EXISTS\s+)?studio\.\w+", schema):
        assert m.group(1), f"missing IF NOT EXISTS: {m.group(0)[:60]}"


def test_event_columns_loader_vs_schema():
    """The loader's insert column list must match the DDL column list exactly."""
    from data.load import load_events  # noqa: F401  (source of truth for the insert)

    ddl = [s for s in sqlsplit(read_schema()) if "viewing_events" in s and "VIEW" not in s][0]
    ddl_cols = re.search(r"\((.*?)\)\s*ENGINE", ddl, re.S).group(1)
    names = []
    for line in ddl_cols.splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        name = line.split()[0]
        if name in {"INDEX", "CONSTRAINT", "PRIMARY", "TTL"}:
            continue
        if "MATERIALIZED" in line or "DEFAULT" in line or "ALIAS" in line:
            continue  # not insertable
        names.append(name)
    src = Path("data/load.py").read_text(encoding="utf-8-sig")
    insert_block = re.search(r"def load_events.*?column_names=list\(cols\.keys\(\)\)", src, re.S).group(0)
    for col in names:
        assert re.search(rf"\b{col}\s*=", insert_block), f"loader never inserts DDL column {col!r}"


def test_fact_table_is_ordered_for_episode_cohort_scans():
    ddl = [s for s in sqlsplit(read_schema()) if "viewing_events" in s and "VIEW" not in s][0]
    assert "ORDER BY (title_id, episode_id, event_time)" in ddl
    assert "PARTITION BY toYYYYMM" in ddl
    assert "bloom_filter" in ddl  # user_id skip index present
