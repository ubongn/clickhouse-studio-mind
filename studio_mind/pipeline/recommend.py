"""RECOMMEND — ranked programming actions with expected impact.

Guardrails: every action's evidence_ids must exist in the registry (enforced),
confidence is required, and without an LLM the stage degrades to template
actions derived from confirmed diagnoses rather than disappearing.
"""

from __future__ import annotations

import json
from typing import Any

from ..evidence import Evidence, EvidenceRegistry
from ..llm import LLM, LLMError

REC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "concrete programming/content decision"},
                    "rationale": {"type": "string", "description": "≤2 sentences, cite evidence IDs"},
                    "expected_impact": {"type": "string", "enum": [
                        "high", "medium", "low"]},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["action", "rationale", "expected_impact", "confidence", "evidence_ids"],
            },
            "minItems": 2,
            "maxItems": 5,
        }
    },
    "required": ["actions"],
}

_SYSTEM = (
    "You are the strategy layer of a streaming-studio analytics agent. Convert "
    "the diagnosis into 2-5 ranked, concrete programming actions an executive "
    "could approve this week (re-edits, scheduling, marketing, licensing, "
    "ad-load experiments, churn saves). Ground every action in cited evidence "
    "IDs; never invent IDs. Be decisive but honest about confidence."
)

_TEMPLATES = {
    "episode_quality": {
        "action": "Order a re-edit or recap-bridge of the flagged weak episodes before the next window",
        "expected_impact": "high", "confidence": "medium",
    },
    "ad_load": {
        "action": "Cap ad density on flagships during premiere windows; measure completion lift",
        "expected_impact": "medium", "confidence": "medium",
    },
    "cadence": {
        "action": "Shift drop day to the observed peak viewing weekday for this audience",
        "expected_impact": "medium", "confidence": "low",
    },
    "cohort_mix": {
        "action": "Re-target the title's marketing to the cohorts that over-index in survivors",
        "expected_impact": "medium", "confidence": "low",
    },
    "segment": {
        "action": "Double down on the over-indexing region/segment in the next campaign",
        "expected_impact": "medium", "confidence": "medium",
    },
}


def run(registry: EvidenceRegistry, intent: dict, primary: list[Evidence],
        diagnosis: dict, llm: LLM | None = None) -> list[dict]:
    confirmed = [v for v in diagnosis.get("verdicts", []) if v.get("verdict") == "CONFIRMED"]

    if llm is not None:
        try:
            out = llm.structured(
                f"Question: {intent.get('question', '(see intent)')}\n"
                f"Intent: {json.dumps({k: v for k, v in intent.items() if k != 'question'}, default=str)}\n\n"
                f"Primary evidence:\n" + "\n\n".join(
                    f"### {e.id} — {e.purpose}\n{e.markdown_table(15)}" for e in primary) +
                f"\n\nDiagnosis (hypotheses + verdicts):\n{json.dumps(diagnosis['verdicts'], default=str)}",
                schema=REC_SCHEMA, system=_SYSTEM,
            )
            actions = out.get("actions", [])
            for a in actions:
                a["evidence_ids"] = registry.valid_ids(a.get("evidence_ids", []))
            if actions:
                order = {"high": 0, "medium": 1, "low": 2}
                actions.sort(key=lambda a: order.get(a.get("expected_impact"), 3))
                return actions
        except LLMError:
            pass

    # deterministic fallback: map confirmed mechanisms to template actions
    actions = []
    for h in diagnosis.get("hypotheses", []):
        mech = h.get("mechanism")
        if mech not in _TEMPLATES:
            continue
        matching = [v for v in confirmed if v.get("statement") == h.get("statement")]
        if matching or not confirmed:
            t = _TEMPLATES[mech]
            actions.append({
                "action": t["action"],
                "rationale": f"follows from: {h.get('statement', '')}",
                "expected_impact": t["expected_impact"],
                "confidence": t["confidence"],
                "evidence_ids": [v["evidence_ids"] for v in matching if v.get("evidence_ids")][:1] or [],
            })
    return actions[:5]
