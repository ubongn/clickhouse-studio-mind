import pytest

from studio_mind.sqlguard import SQLRejected, validate_sql


def test_select_passthrough_and_limit_injection():
    out = validate_sql("SELECT 1 AS x")
    assert out.endswith("LIMIT 10000")


def test_limit_injection_and_clamp():
    big = validate_sql("SELECT number FROM system.numbers LIMIT 999999")
    assert big.endswith("LIMIT 10000")


def test_system_reads_allowed():
    """Schema introspection and query_log (trust panel) must pass the guard."""
    for q in (
        "SELECT name, total_rows FROM system.tables WHERE database = 'studio'",
        "SELECT read_rows, query_duration_ms FROM system.query_log "
        "WHERE type = 'QueryFinish' ORDER BY event_time DESC LIMIT 5",
    ):
        assert validate_sql(q).upper().startswith("SELECT")


def test_write_keywords_rejected():
    for bad in (
        "INSERT INTO viewing_events VALUES (1)",
        "DROP TABLE viewing_events",
        "TRUNCATE TABLE users",
        "CREATE TABLE x (a UInt8) ENGINE = Memory",
        "ALTER TABLE titles DELETE WHERE 1",
        "KILL QUERY WHERE query_id = 'x'",
        "OPTIMIZE TABLE viewing_events FINAL",
    ):
        with pytest.raises(SQLRejected):
            validate_sql(bad)


def test_first_token_must_be_select_or_with():
    with pytest.raises(SQLRejected):
        validate_sql("SYSTEM MERGES ON table")
    with pytest.raises(SQLRejected):
        validate_sql("EXPLAIN SELECT 1")
    assert validate_sql("WITH x AS (SELECT 1) SELECT * FROM x").startswith("WITH")


def test_multiple_statements_and_comments_rejected():
    with pytest.raises(SQLRejected):
        validate_sql("SELECT 1; DROP TABLE titles")
    with pytest.raises(SQLRejected):
        validate_sql("   ")
    # comments are stripped, not fatal
    assert validate_sql("SELECT 1 -- trailing comment").startswith("SELECT")


def test_custom_max_limit():
    assert validate_sql("SELECT 1", max_limit=50).endswith("LIMIT 50")
    assert validate_sql("SELECT 1 LIMIT 500", max_limit=50).endswith("LIMIT 50")
