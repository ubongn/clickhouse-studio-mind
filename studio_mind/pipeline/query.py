"""QUERY — intent → validated ClickHouse SQL → executed evidence.

Architecture note (this is deliberate): canonical executive questions compile
through DETERMINISTIC patterns (real ClickHouse SQL, parameter-bound). The LLM
authors SQL only for the long tail those patterns don't cover — and whatever it
writes passes the same sqlguard + EXPLAIN + sanity checks. No query reaches
ClickHouse unvalidated; no number enters a brief without evidence.
"""

from __future__ import annotations

import re
from typing import Any

from .. import ch
from ..evidence import EvidenceRegistry
from ..llm import LLM, LLMError

# ---------------------------------------------------------------------------
# deterministic pattern compiler
# ---------------------------------------------------------------------------


def _filters(intent: dict) -> str:
    """WHERE clauses for segment filters on the events table (alias v)."""
    f = []
    if intent.get("region"):
        f.append(f"v.region = '{intent['region']}'")
    if intent.get("plan"):
        f.append(f"v.plan = '{intent['plan']}'")
    if intent.get("device"):
        f.append(f"v.device = '{intent['device']}'")
    if intent.get("time_start") and intent.get("time_end"):
        f.append(f"v.event_date BETWEEN '{intent['time_start']}' AND '{intent['time_end']}'")
    return (" AND " + " AND ".join(f)) if f else ""


def compile_pattern(intent: dict) -> list[tuple[str, str]] | None:
    """Return [(purpose, sql), …] for a supported intent, else None."""
    atype = intent.get("analysis_type")
    tid = intent.get("title_id")

    if atype == "episode_funnel" and tid:
        flt = _filters(intent)
        return [
            (
                f"Per-episode funnel for title_id={tid}: unique viewers, completion, plays",
                f"""
SELECT e.ep_number                        AS episode,
       uniqExact(v.user_id)               AS unique_viewers,
       round(avg(v.completion_pct), 3)    AS avg_completion,
       count()                            AS plays,
       round(sum(v.watched_seconds)/3600, 0) AS hours_watched
FROM viewing_events v
INNER JOIN episodes e ON e.episode_id = v.episode_id
WHERE v.title_id = {tid}{flt}
GROUP BY episode
ORDER BY episode
""",
            ),
            (
                "Audience retention between consecutive episodes (who came back next week)",
                f"""
WITH per_ep AS (
    SELECT e.ep_number AS ep, v.user_id
    FROM viewing_events v
    INNER JOIN episodes e ON e.episode_id = v.episode_id
    WHERE v.title_id = {tid}
    GROUP BY ep, user_id
)
SELECT a.ep                                        AS episode,
       uniqExactIf(a.user_id, b.user_id != 0)      AS returned_next_ep,
       round(uniqExactIf(a.user_id, b.user_id != 0) / uniqExact(a.user_id), 3) AS return_rate
FROM per_ep a
LEFT JOIN per_ep b ON a.user_id = b.user_id AND b.ep = a.ep + 1
GROUP BY episode
ORDER BY episode
""",
            ),
        ]

    if atype == "retention":
        seg = []
        if intent.get("acquisition_channel"):
            seg.append(f"AND u.acquisition_channel = '{intent['acquisition_channel']}'")
        if intent.get("plan"):
            seg.append(f"AND u.plan = '{intent['plan']}'")
        if intent.get("region"):
            seg.append(f"AND u.region = '{intent['region']}'")
        seg_sql = (" " + " ".join(seg)) if seg else ""
        t0 = intent.get("time_start") or "2024-07-01"
        t1 = intent.get("time_end") or "2026-07-31"
        return [
            (
                "Cohort retention grid: active users per weeks-since-signup, by signup month",
                f"""
SELECT formatDateTime(toStartOfMonth(u.signup_date), '%Y-%m') AS cohort,
       toRelativeWeekNum(v.event_date) - toRelativeWeekNum(toStartOfMonth(u.signup_date)) AS wk,
       uniqExact(v.user_id) AS active_users
FROM viewing_events v
INNER JOIN users u USING (user_id)
WHERE u.signup_date BETWEEN '{t0}' AND '{t1}'
  AND v.event_date >= u.signup_date{seg_sql}
GROUP BY cohort, wk
ORDER BY cohort, wk
""",
            ),
        ]

    if atype == "segment":
        dim_map = {
            "region": "v.region", "plan": "v.plan", "device": "v.device",
            "genre": "t.genre", "acquisition_channel": "u.acquisition_channel",
        }
        # explicit "which genres ..." grouping wins; else first set filter dim
        gb = intent.get("group_by")
        dim = dim_map.get(gb) if gb else None
        if dim is None:
            dim = next((dim_map[k] for k in dim_map if intent.get(k)), None)
        if dim is None:
            dim = "v.region"
        join_t = " INNER JOIN titles t ON t.title_id = v.title_id" if dim == "t.genre" else ""
        join_u = " INNER JOIN users u ON u.user_id = v.user_id" if "u." in dim else ""
        flt = _filters(intent) if not (dim == "v.region" and intent.get("region")) else ""
        return [
            (
                f"Segment cut by {dim}: completion, unique viewers, hours",
                f"""
SELECT {dim} AS segment,
       uniqExact(v.user_id)               AS unique_viewers,
       round(avg(v.completion_pct), 3)    AS avg_completion,
       round(sum(v.watched_seconds)/3600, 0) AS hours_watched,
       round(avg(v.ad_impressions), 2)    AS avg_ads_per_play
FROM viewing_events v{join_t}{join_u}
WHERE 1=1{flt}
GROUP BY segment
ORDER BY unique_viewers DESC
""",
            ),
        ]

    if atype == "churn":
        grp = intent.get("acquisition_channel") and "u.acquisition_channel" or (
            intent.get("plan") and "u.plan" or (
                intent.get("region") and "u.region" or "u.acquisition_channel"))
        g = grp.split(".")[-1]
        return [
            (
                f"Churn rate by {g}",
                f"""
SELECT {grp} AS {g},
       count() AS users,
       countIf(u.churned) AS churned_users,
       round(countIf(u.churned) / count(), 3) AS churn_rate,
       round(avgIf(u.activity_per_30d, u.churned = 0), 1) AS avg_active_days_of_retained
FROM users u
GROUP BY {g}
ORDER BY churn_rate DESC
""",
            ),
        ]

    if atype == "engagement" and tid:
        return [
            (
                f"Daily viewers & completion for title_id={tid} (drop cadence visible)",
                f"""
SELECT toDate(v.event_time) AS day,
       uniqExact(v.user_id) AS viewers,
       round(avg(v.completion_pct), 3) AS avg_completion
FROM viewing_events v
WHERE v.title_id = {tid}{_filters(intent)}
GROUP BY day
ORDER BY day
""",
            ),
        ]

    if atype == "engagement":
        return [
            (
                "Platform engagement over time: DAU, hours, completion",
                f"""
SELECT toDate(event_time) AS day,
       uniqExact(user_id) AS dau,
       round(sum(watched_seconds)/3600, 0) AS hours,
       round(avg(completion_pct), 3) AS avg_completion
FROM viewing_events v
WHERE 1=1{_filters(intent)}
GROUP BY day
ORDER BY day
""",
            ),
        ]

    return None


# ---------------------------------------------------------------------------
# LLM-authored tail queries (validated like everything else)
# ---------------------------------------------------------------------------

_LLM_SYSTEM = (
    "You write read-only ClickHouse SQL for a streaming-analytics warehouse. "
    "Return ONE query only. Use uniqExact for distinct counts, round() for ratios, "
    "alias every column clearly. Filter dimensions to exactly what the intent asks. "
    "Never SELECT *; aggregate. Always include a LIMIT <= 500. "
    "Tables (database studio): viewing_events(event_time DateTime64, event_date Date, "
    "user_id, title_id, episode_id, season_no, ep_number, watched_seconds, "
    "content_seconds, completion_pct Float32 0..1, completed Bool, ad_impressions, "
    "ad_seconds, device, region, plan, is_binge, session_pos), "
    "users(user_id, signup_date, region, plan, primary_device, acquisition_channel, "
    "activity_per_30d, churned, churn_date, is_active, last_active_date), "
    "titles(title_id, title_name, title_type, genre, seasons, total_episodes, "
    "avg_ep_min, cadence, quality_arc, popularity, is_original, release_dow, ad_density), "
    "episodes(episode_id, title_id, season_no, ep_number, runtime_min, quality_score)."
)


def llm_query(intent: dict, question: str, llm: LLM, schema_brief: str) -> list[tuple[str, str]]:
    prompt = (
        f"Executive question: {question}\n\nAnalytics intent (JSON): {intent}\n\n"
        f"Live schema:\n{schema_brief}\n\n"
        "Write the single best ClickHouse query to answer this. Output SQL only."
    )
    sql = llm.generate(prompt, system=_LLM_SYSTEM)
    sql = re.sub(r"```(sql)?|```", "", sql).strip()
    return [("LLM-authored query for the intent", sql)]


# ---------------------------------------------------------------------------
# stage entry
# ---------------------------------------------------------------------------


def run(client, registry: EvidenceRegistry, intent: dict, question: str,
        llm: LLM | None = None, schema_brief: str = "") -> list:
    """Compile + execute. Returns the evidence list for downstream stages."""
    pattern = compile_pattern(intent)
    if pattern is not None:
        jobs = pattern
        source = "pattern"
    elif llm is not None:
        for attempt in range(2):
            try:
                jobs = llm_query(intent, question, llm, schema_brief)
                break
            except LLMError:
                if attempt == 1:
                    jobs = []
        source = "llm"
    else:
        jobs, source = [], "none"

    results = []
    for purpose, sql in jobs:
        ev = ch.run_query(client, registry, purpose, sql)
        results.append(ev)
        if ev.error:
            if source == "llm":
                # one repair round: show the error, ask for a fixed query
                try:
                    fixed = llm.generate(
                        f"The query failed.\nSQL:\n{sql}\nError:\n{ev.error}\n"
                        "Return the corrected SQL only.",
                        system=_LLM_SYSTEM,
                    )
                    fixed = re.sub(r"```(sql)?|```", "", fixed).strip()
                    ev2 = ch.run_query(client, registry, purpose + " (repaired)", fixed)
                    results.append(ev2)
                except LLMError:
                    pass
    return [e for e in results if e.ok], source


def sanity_check(evidence_list: list) -> list[str]:
    """Flag suspicious evidence: empty results, single-row nothing-burgers."""
    notes = []
    for ev in evidence_list:
        if ev.row_count == 0:
            notes.append(f"{ev.id}: 0 rows — filters may be too narrow")
        elif ev.row_count == 1 and "summary" not in ev.purpose.lower():
            notes.append(f"{ev.id}: single row — is that really a comparison?")
    return notes
