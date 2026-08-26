"""Schema apply + columnar load + post-load verification.

Insert path is clickhouse-connect's columnar insert (Native format over HTTP),
batched at ~2M rows — the same transport any production pipeline would use
against a remote ClickHouse or ClickHouse Cloud.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
BATCH_ROWS = 2_000_000

DEVICES = ("tv", "mobile", "desktop", "tablet")
REGIONS = ("NA", "EMEA", "APAC", "LATAM")
PLANS = ("basic_ads", "standard", "premium")


def apply_schema(client, database: str = "studio") -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8-sig")   # tolerate BOM
    # schema.sql creates database `studio` explicitly; keep database name fixed
    for stmt in sqlsplit(sql):
        client.command(stmt)


def sqlsplit(sql: str):
    """Split on semicolons at top level (no procedures in this schema)."""
    out, buf = [], []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.strip().endswith(";"):
            out.append("\n".join(buf).strip().rstrip(";"))
            buf = []
    rest = "\n".join(buf).strip().rstrip(";")
    if rest:
        out.append(rest)
    return [s for s in out if s.strip()]


def _batches(n: int):
    for i in range(0, n, BATCH_ROWS):
        yield i, min(i + BATCH_ROWS, n)


def load_titles(client, catalog: dict, database: str = "studio") -> None:
    client.insert(f"{database}.titles", catalog["titles"],
                  column_names=["title_id", "title_name", "title_type", "genre", "seasons",
                                "total_episodes", "avg_ep_min", "cadence", "quality_arc",
                                "popularity", "is_original", "release_dow", "ad_density"])
    client.insert(f"{database}.episodes", catalog["episodes"],
                  column_names=["episode_id", "title_id", "season_no", "ep_number",
                                "runtime_min", "quality_score"])
    print(f"[load]    titles={len(catalog['titles'])} episodes={len(catalog['episodes'])}")


def load_users(client, users: dict, churned, churn_date, last_day, database: str = "studio") -> None:
    n = users["user_id"].size
    signup = users["signup"].astype("datetime64[D]")
    rows = list(zip(
        users["user_id"].tolist(),
        signup.tolist(),
        [REGIONS[i] for i in users["region"]],
        [PLANS[i] for i in users["plan"]],
        [DEVICES[i] for i in users["device"]],
        [("paid_social", "search", "partnership", "organic", "referral")[i] for i in users["channel"]],
        users["activity"].tolist(),
        churned.tolist(),
        churn_date.tolist(),
        (~churned).tolist(),
        last_day.tolist(),
    ))
    client.insert(f"{database}.users", rows,
                  column_names=["user_id", "signup_date", "region", "plan", "primary_device",
                                "acquisition_channel", "activity_per_30d", "churned",
                                "churn_date", "is_active", "last_active_date"])
    print(f"[load]    users={n:,}")


def load_events(client, out: dict, database: str = "studio") -> int:
    n = sum(len(a) for a in out["event_time_ms"])
    t0 = time.time()
    loaded = 0
    for lo, hi in _batches(n):
        ev_ms = np.concatenate(out["event_time_ms"])[lo:hi]
        ev_dt64 = (ev_ms.astype("datetime64[ms]"))
        cols = dict(
            event_time=ev_dt64,
            user_id=np.concatenate(out["user_id"])[lo:hi],
            title_id=np.concatenate(out["title_id"])[lo:hi],
            episode_id=np.concatenate(out["episode_id"])[lo:hi],
            season_no=np.concatenate(out["season_no"])[lo:hi],
            ep_number=np.concatenate(out["ep_number"])[lo:hi],
            watched_seconds=np.concatenate(out["watched_seconds"])[lo:hi],
            content_seconds=np.concatenate(out["content_seconds"])[lo:hi],
            completion_pct=np.concatenate(out["completion_pct"])[lo:hi],
            completed=np.concatenate(out["completed"])[lo:hi],
            ad_impressions=np.concatenate(out["ad_impressions"])[lo:hi],
            ad_seconds=np.concatenate(out["ad_seconds"])[lo:hi],
            rebuffer_count=np.concatenate(out["rebuffer_count"])[lo:hi],
            rebuffer_seconds=np.concatenate(out["rebuffer_seconds"])[lo:hi],
            device=np.array(DEVICES, dtype=object)[np.concatenate(out["device_idx"])[lo:hi]],
            region=np.array(REGIONS, dtype=object)[np.concatenate(out["region_idx"])[lo:hi]],
            plan=np.array(PLANS, dtype=object)[np.concatenate(out["plan_idx"])[lo:hi]],
            is_binge=np.concatenate(out["is_binge"])[lo:hi],
            session_pos=np.concatenate(out["session_pos"])[lo:hi],
        )
        client.insert(f"{database}.viewing_events", cols,
                      column_names=list(cols.keys()))
        loaded += hi - lo
        dt = time.time() - t0
        print(f"[load]    events {loaded:,}/{n:,} ({loaded/max(dt,0.01):,.0f} rows/s)")
    return loaded


def verify(client, database: str = "studio") -> None:
    print("[verify]  running sanity queries …")
    checks = [
        ("row count", f"SELECT count() FROM {database}.viewing_events"),
        ("distinct users active", f"SELECT uniqExact(user_id) FROM {database}.viewing_events"),
        ("Nightfall funnel (viewers/avg completion by ep)",
         f"""
         SELECT e.ep_number,
                count() AS plays,
                round(avg(v.completion_pct), 3) AS avg_completion
         FROM {database}.viewing_events v
         JOIN {database}.episodes e USING (episode_id)
         WHERE v.title_id = 1
         GROUP BY e.ep_number ORDER BY e.ep_number
         """),
        ("MV episode_stats works",
         f"""
         SELECT ep_number, uniqMerge(viewers) AS viewers, round(avgMerge(avg_completion), 3) AS avg_c
         FROM {database}.episode_stats WHERE title_id = 1 GROUP BY ep_number ORDER BY ep_number
         """),
    ]
    for label, q in checks:
        res = client.query(q)
        rows = res.result_rows
        if "funnel" in label or "MV" in label:
            print(f"[verify]  {label}:")
            for r in rows:
                print(f"            {r}")
        else:
            print(f"[verify]  {label}: {rows}")


def load_all(client, catalog, users, out, churned, churn_date, last_day, database: str = "studio"):
    print("[schema]  applying DDL …")
    apply_schema(client, database)
    load_titles(client, catalog, database)
    load_users(client, users, churned, churn_date, last_day, database)
    load_events(client, out, database)
    # finalize MV parts so uniqMerge sees consolidated state quickly
    try:
        client.command(f"OPTIMIZE TABLE {database}.episode_stats FINAL")
        client.command(f"OPTIMIZE TABLE {database}.title_daily FINAL")
        client.command(f"OPTIMIZE TABLE {database}.region_plan_daily FINAL")
    except Exception as e:  # pragma: no cover — OPTIMIZE is advisory
        print(f"[load]    optimize skipped: {e}")
    verify(client, database)
