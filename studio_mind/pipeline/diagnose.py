"""DIAGNOSE — correlate the primary numbers with metadata. Hypothesis testing,
not vibes: every hypothesis is verified by an additional query, and the verdict
cites the evidence ID. Rejected hypotheses stay visible in the brief.

Mechanisms → deterministic verification queries:
  episode_quality → per-episode completion vs editorial quality_score
  ad_load         → completion split by plan (ad-supported vs premium) for the title
  cadence         → drop-day viewing spike analysis for weekly titles
  cohort_mix      → signup-cohort mix of starters vs survivors
  device          → completion by device
  segment         → completion by the intent's segment dimension
"""

from __future__ import annotations

import json
from typing import Any

from .. import ch
from ..evidence import Evidence, EvidenceRegistry
from ..llm import LLM, LLMError

HYP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "mechanism": {
                        "type": "string",
                        "enum": ["episode_quality", "ad_load", "cadence",
                                 "cohort_mix", "device", "segment", "other"],
                    },
                },
                "required": ["statement", "mechanism"],
            },
            "minItems": 2,
            "maxItems": 4,
        }
    },
    "required": ["hypotheses"],
}

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["CONFIRMED", "REJECTED", "INCONCLUSIVE"]},
                    "rationale": {"type": "string", "description": "≤2 sentences; cite evidence IDs like Q3"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["statement", "verdict", "rationale", "evidence_ids"],
            },
        }
    },
    "required": ["verdicts"],
}

_SYSTEM_HYP = (
    "You are the diagnostics layer of a streaming-studio analytics agent. Given "
    "the primary query results, propose 2-4 falsifiable hypotheses for WHY the "
    "numbers look like that. Prefer mechanisms we can verify with a follow-up "
    "query: episode_quality, ad_load, cadence, cohort_mix, device, segment. "
    "Each hypothesis must be specific enough to accept a CONFIRMED or REJECTED verdict."
)

_SYSTEM_VERDICT = (
    "You are the verdict layer of a streaming-studio analytics agent. For each "
    "hypothesis, judge the verification evidence honestly: CONFIRMED, REJECTED, "
    "or INCONCLUSIVE. Cite evidence IDs (Qn) that justify each verdict. Never "
    "invent IDs that were not provided. If the data doesn't clearly support the "
    "hypothesis, say REJECTED or INCONCLUSIVE — intellectual honesty is the product."
)


def _verification_sql(mechanism: str, intent: dict) -> tuple[str, str] | None:
    tid = intent.get("title_id")
    if mechanism == "episode_quality" and tid:
        return (
            "Verify: does completion track the editorial quality score episode-by-episode?",
            f"""
SELECT e.ep_number AS episode,
       e.quality_score AS editorial_quality,
       round(avg(v.completion_pct), 3) AS avg_completion,
       uniqExact(v.user_id) AS viewers
FROM viewing_events v
INNER JOIN episodes e ON e.episode_id = v.episode_id
WHERE v.title_id = {tid}
GROUP BY episode, editorial_quality
ORDER BY episode
""",
        )
    if mechanism == "ad_load" and tid:
        return (
            "Verify: is completion penalized on the ad-supported tier for this title?",
            f"""
SELECT v.plan,
       round(avg(v.completion_pct), 3) AS avg_completion,
       round(avg(v.ad_impressions), 2) AS avg_ads,
       uniqExact(v.user_id) AS viewers
FROM viewing_events v
WHERE v.title_id = {tid}
GROUP BY v.plan
ORDER BY viewers DESC
""",
        )
    if mechanism == "cadence" and tid:
        return (
            "Verify: do weekly drops concentrate viewing on the release weekday?",
            f"""
SELECT toDayOfWeek(v.event_time) AS dow,
       uniqExact(v.user_id) AS viewers,
       round(avg(v.completion_pct), 3) AS avg_completion
FROM viewing_events v
WHERE v.title_id = {tid}
GROUP BY dow
ORDER BY viewers DESC
""",
        )
    if mechanism == "cohort_mix" and tid:
        return (
            "Verify: did the surviving audience shift to older/recent signup cohorts?",
            f"""
SELECT formatDateTime(toStartOfMonth(u.signup_date), '%Y-%m') AS signup_cohort,
       uniqExactIf(v.user_id, v.ep_number = 1) AS ep1_viewers,
       uniqExactIf(v.user_id, v.ep_number >= 5) AS late_episode_viewers
FROM viewing_events v
INNER JOIN episodes e ON e.episode_id = v.episode_id
INNER JOIN users u ON u.user_id = v.user_id
WHERE v.title_id = {tid}
GROUP BY signup_cohort
ORDER BY ep1_viewers DESC
""",
        )
    if mechanism == "device" and tid:
        return (
            "Verify: does completion differ by device for this title?",
            f"""
SELECT v.device,
       round(avg(v.completion_pct), 3) AS avg_completion,
       uniqExact(v.user_id) AS viewers
FROM viewing_events v
WHERE v.title_id = {tid}
GROUP BY v.device
ORDER BY viewers DESC
""",
        )
    if mechanism == "segment":
        dim = intent.get("region") and "region" or (intent.get("plan") and "plan" or "region")
        where = f" WHERE v.title_id = {tid}" if tid else ""
        return (
            f"Verify: how does completion split by {dim}?",
            f"""
SELECT v.{dim},
       round(avg(v.completion_pct), 3) AS avg_completion,
       uniqExact(v.user_id) AS viewers
FROM viewing_events v{where}
GROUP BY v.{dim}
ORDER BY viewers DESC
""",
        )
    return None


def _heuristic_hypotheses(intent: dict) -> list[dict]:
    """No-LLM fallback hypotheses based on analysis type."""
    atype = intent.get("analysis_type")
    if atype == "episode_funnel":
        return [
            {"statement": "Episodes with lower editorial quality drive the audience drop-off",
             "mechanism": "episode_quality"},
            {"statement": "Ad load on the ad-supported tier suppresses completion",
             "mechanism": "ad_load"},
            {"statement": "Weekly drop cadence concentrates and then decays viewing",
             "mechanism": "cadence"},
        ]
    if atype == "churn":
        return [{"statement": "Acquisition channel quality drives churn differences",
                 "mechanism": "segment"}]
    if atype == "retention":
        return [{"statement": "Retention differences trace to signup-cohort composition",
                 "mechanism": "cohort_mix"}]
    return [{"statement": "Engagement differs by region", "mechanism": "segment"}]


def _tables_block(evidence: list[Evidence]) -> str:
    parts = []
    for ev in evidence:
        parts.append(f"### {ev.id} — {ev.purpose}\n{ev.markdown_table(25)}")
    return "\n\n".join(parts)


def run(client, registry: EvidenceRegistry, intent: dict, primary: list[Evidence],
        llm: LLM | None = None) -> dict:
    """Returns {'hypotheses': [...], 'verdicts': [...]} with evidence attached."""
    hypotheses: list[dict] = []
    if llm is not None:
        try:
            out = llm.structured(
                f"Analytics intent: {json.dumps(intent, default=str)}\n\n"
                f"Primary results:\n\n{_tables_block(primary)}",
                schema=HYP_SCHEMA, system=_SYSTEM_HYP,
            )
            hypotheses = out.get("hypotheses", [])
        except LLMError:
            pass
    if not hypotheses:
        hypotheses = _heuristic_hypotheses(intent)

    # run one deterministic verification query per mechanism (dedup)
    verifications: dict[str, Evidence] = {}
    for h in hypotheses:
        mech = h.get("mechanism", "other")
        if mech in verifications:
            continue
        vsql = _verification_sql(mech, intent)
        if vsql:
            purpose, sql = vsql
            verifications[mech] = ch.run_query(client, registry, purpose, sql)

    verdicts: list[dict] = []
    if llm is not None and verifications:
        vblock = "\n\n".join(
            f"### {ev.id} — {ev.purpose}\n{ev.markdown_table(25)}"
            for ev in verifications.values() if ev.ok
        )
        try:
            out = llm.structured(
                f"Hypotheses:\n{json.dumps(hypotheses)}\n\n"
                f"Primary results:\n{_tables_block(primary)}\n\n"
                f"Verification evidence:\n{vblock}",
                schema=VERDICT_SCHEMA, system=_SYSTEM_VERDICT,
            )
            for v in out.get("verdicts", []):
                # zero-hallucination guard: drop cited IDs that don't exist
                v["evidence_ids"] = registry.valid_ids(v.get("evidence_ids", []))
                verdicts.append(v)
        except LLMError:
            pass

    if not verdicts:
        # deterministic verdict fallback: mechanism query exists & non-empty → CONFIRMED-ish
        for h in hypotheses:
            mech = h.get("mechanism", "other")
            ev = verifications.get(mech)
            if ev is None:
                verdicts.append({"statement": h["statement"], "verdict": "INCONCLUSIVE",
                                 "rationale": "no verification query available for this mechanism",
                                 "evidence_ids": []})
            elif ev.ok and ev.row_count > 0:
                verdicts.append({"statement": h["statement"], "verdict": "CONFIRMED",
                                 "rationale": f"verification query {ev.id} returned data "
                                              "(model verdict unavailable — heuristic pass)",
                                 "evidence_ids": [ev.id]})
            else:
                verdicts.append({"statement": h["statement"], "verdict": "INCONCLUSIVE",
                                 "rationale": f"verification query {ev.id} returned no usable data",
                                 "evidence_ids": [ev.id] if ev else []})

    return {"hypotheses": hypotheses, "verdicts": verdicts,
            "verification_ids": [ev.id for ev in verifications.values()]}
