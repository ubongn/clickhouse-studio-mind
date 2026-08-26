"""Schema unit tests — shape, ordering, determinism (engine tests live in CI)."""

from __future__ import annotations

import pytest

from studio_mind import schema


def test_full_ddl_runs_database_first_then_tables_then_views():
    stmts = schema.ordered_statements()
    assert stmts[0].startswith("CREATE DATABASE")
    assert "studio.titles" in stmts[1]
    assert "studio.viewing_events" in stmts[4]
    # every materialized view comes after the fact table it reads
    fact_idx = next(i for i, s in enumerate(stmts) if "studio.viewing_events" in s)
    mv_idx = next(i for i, s in enumerate(stmts) if "mv_daily_title_audience" in s)
    assert mv_idx > fact_idx


def test_rebuild_prepends_reverse_order_drops():
    stmts = schema.ordered_statements(rebuild=True)
    drop_section = stmts[:7]
    assert drop_section[0].startswith("DROP VIEW IF EXISTS studio.mv_")
    assert drop_section[1].startswith("DROP VIEW IF EXISTS studio.mv_")
    # tables dropped after views, database dropped after its tables
    assert drop_section[2:6] == [f"DROP TABLE IF EXISTS studio.{n}" for n in schema.TABLE_STATEMENTS]
    assert drop_section[6] == "DROP DATABASE IF EXISTS studio"
    assert stmts[7].startswith("CREATE DATABASE")


def test_fact_table_orders_title_first_for_episode_cohort_scans():
    ddl = schema.TABLE_STATEMENTS["viewing_events"]
    assert "ORDER BY (title_id, episode_id, event_time, user_id)" in ddl
    assert "PARTITION BY toYYYYMM(event_time)" in ddl


def test_mvs_use_state_aggregates_for_merge_side_reads():
    daily = schema.MATERIALIZED_VIEW_STATEMENTS["mv_daily_title_audience"]
    health = schema.MATERIALIZED_VIEW_STATEMENTS["mv_episode_health"]
    assert "uniqExactState(user_id)" in daily and "uniqExactState(user_id)" in health
    assert "AggregatingMergeTree" in daily and "AggregatingMergeTree" in health
    assert "quantileState(0.5)(completion_pct)" in health


def test_low_cardinality_on_all_hot_dimensions():
    import re

    fact = schema.TABLE_STATEMENTS["viewing_events"]
    for dim in ("device", "playback_quality", "exit_reason"):
        assert re.search(rf"{dim}\s+LowCardinality\(String\)", fact), dim
    users = schema.TABLE_STATEMENTS["users"]
    for dim in ("country", "plan_tier", "acquisition"):
        assert re.search(rf"{dim}\s+LowCardinality\(String\)", users), dim


def test_apply_schema_executes_statements_via_client():
    calls: list[str] = []

    class FakeClient:
        def command(self, stmt):
            calls.append(stmt)

    executed = schema.apply_schema(FakeClient())
    assert len(executed) == len(calls)
    assert executed[0].startswith("CREATE DATABASE")
    assert executed[-1].splitlines()[0].startswith("CREATE MATERIALIZED VIEW")


def test_apply_schema_rebuild_drops_first():
    calls: list[str] = []

    class FakeClient:
        def command(self, stmt):
            calls.append(stmt)

    schema.apply_schema(FakeClient(), rebuild=True)
    assert calls[0].startswith("DROP VIEW")


@pytest.mark.parametrize("name", list(schema.TABLE_STATEMENTS) + list(schema.MATERIALIZED_VIEW_STATEMENTS))
def test_statements_are_idempotent_create_if_not_exists(name):
    pool = {**schema.TABLE_STATEMENTS, **schema.MATERIALIZED_VIEW_STATEMENTS}
    assert "IF NOT EXISTS" in pool[name]
