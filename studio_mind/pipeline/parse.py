"""PARSE — exec question → structured analytics intent.

Two engines, in priority order:
  1. Gemini structured output (schema-enforced) — handles nuance
  2. Deterministic keyword parser — demo safety net; the pipeline never dies
     because the model is down.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from ..llm import LLM, LLMError

INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "analysis_type": {
            "type": "string",
            "enum": ["episode_funnel", "retention", "segment", "churn",
                     "engagement", "comparison", "summary"],
        },
        "title": {"type": ["string", "null"], "description": "exact title name if the question is about one show/film"},
        "genre": {"type": ["string", "null"]},
        "region": {"type": ["string", "null"], "enum": ["NA", "EMEA", "APAC", "LATAM", None]},
        "plan": {"type": ["string", "null"], "enum": ["basic_ads", "standard", "premium", None]},
        "device": {"type": ["string", "null"], "enum": ["tv", "mobile", "desktop", "tablet", None]},
        "acquisition_channel": {"type": ["string", "null"],
                                 "enum": ["paid_social", "search", "partnership", "organic", "referral", None]},
        "time_start": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "time_end": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "metrics": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["analysis_type", "metrics"],
}

SYSTEM = (
    "You are the intent parser inside a streaming-studio analytics agent. "
    "Convert the executive's question into a precise analytics intent. "
    "Only set a filter field if the question clearly implies it; use null otherwise. "
    "analysis_type: episode_funnel (per-episode audience/completion of one title), "
    "retention (cohort activity over time), segment (compare groups), churn "
    "(churn rates/drivers), engagement (activity over time), comparison (A vs B), "
    "summary (general overview). time defaults: leave null for the full window "
    "2026-02-01..2026-07-31 unless the question narrows it."
)

_KEYWORDS_TYPE = [
    (("episode", "funnel", "audience by episode", "drop-off", "lost", "lose", "bled", "viewers by episode", "completion by episode"), "episode_funnel"),
    (("retain", "retention", "cohort", "come back", "returning"), "retention"),
    (("churn", "cancel", "quit", "left the platform"), "churn"),
    (("compare", "versus", " vs ", "better", "worse", "which region", "which genre", "which plan", "segment"), "segment"),
    (("engagement", "watch time", "hours watched", "activity"), "engagement"),
]

_REGIONS = {"na": "NA", "north america": "NA", "emea": "EMEA", "europe": "EMEA",
            "apac": "APAC", "asia": "APAC", "latam": "LATAM", "latin america": "LATAM"}
_PLANS = {"basic": "basic_ads", "ad-supported": "basic_ads", "ads": "basic_ads",
          "standard": "standard", "premium": "premium", "ad-free": "premium"}
_DEVICES = {"tv": "tv", "mobile": "mobile", "phone": "mobile", "desktop": "desktop",
            "web": "desktop", "tablet": "tablet"}


def _default_window() -> tuple[str, str]:
    end = date(2026, 7, 31)
    return "2026-02-01", end.isoformat()


def fallback_parse(question: str, titles: list[tuple[int, str]] | None = None) -> dict:
    q = question.lower()
    intent: dict[str, Any] = {"analysis_type": "summary", "metrics": ["unique_viewers", "avg_completion"]}

    for keys, atype in _KEYWORDS_TYPE:
        if any(k in q for k in keys):
            intent["analysis_type"] = atype
            break

    if titles:
        for tid, name in sorted(titles, key=lambda t: -len(t[1])):
            if name.lower() in q:
                intent["title"] = name
                intent["title_id"] = tid
                break
    for k, v in _REGIONS.items():
        if k in q:
            intent["region"] = v
            break
    for k, v in _PLANS.items():
        if re.search(rf"\b{re.escape(k)}\b", q):
            intent["plan"] = v
            break
    for k, v in _DEVICES.items():
        if re.search(rf"\b{re.escape(k)}\b", q):
            intent["device"] = v
            break
    for ch in ("paid_social", "search", "partnership", "organic", "referral"):
        if ch.replace("_", " ") in q or ch in q:
            intent["acquisition_channel"] = ch
            break

    start, end = _default_window()
    intent.setdefault("time_start", start)
    intent.setdefault("time_end", end)
    return intent


def parse(question: str, llm: LLM | None = None,
          titles: list[tuple[int, str]] | None = None) -> dict:
    """Structured intent. Tries Gemini; falls back to the keyword parser."""
    if llm is not None:
        glossary = ""
        if titles:
            sample = ", ".join(n for _, n in titles[:60])
            glossary = f"\nKnown titles (use exact names): {sample}"
        try:
            out = llm.structured(
                f"Executive question:\n\"{question}\"\n{glossary}",
                schema=INTENT_SCHEMA, system=SYSTEM,
            )
            out.setdefault("time_start", _default_window()[0])
            out.setdefault("time_end", _default_window()[1])
            # bind title name → id deterministically from the warehouse glossary
            if out.get("title") and titles:
                for tid, name in titles:
                    if name.lower() == out["title"].lower():
                        out["title_id"] = tid
                        out["title"] = name
                        break
                else:
                    out.pop("title", None)
            return out
        except LLMError:
            pass
    return fallback_parse(question, titles)
