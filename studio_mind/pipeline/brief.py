"""BRIEF — the one-page decision brief.

The signature feature: every claim in the brief carries [Qn] evidence markers
that resolve to the exact SQL + result table. The builder validates every
marker against the registry and appends the full evidence appendix, so the
brief is self-contained and independently checkable.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ..evidence import Evidence, EvidenceRegistry

_MARKER = re.compile(r"\[(Q\d+)\]")


def _clean_citations(text: str, registry: EvidenceRegistry) -> str:
    """Remove [Qn] markers that don't resolve — invalid citations never ship."""
    def _sub(m: re.Match) -> str:
        return m.group(0) if registry.get(m.group(1)) is not None else ""

    return _MARKER.sub(_sub, text)


def build(question: str, intent: dict, primary: list[Evidence],
          diagnosis: dict, actions: list[dict],
          registry: EvidenceRegistry, meta: dict | None = None) -> str:
    meta = meta or {}
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    add = lines.append

    add(f"# Decision brief — {question.strip()}")
    add(f"*Generated {ts} · ClickHouse Studio Mind · every number cites its query "
        f"([Qn] markers resolve in the appendix)*")
    add("")

    # headline findings from primary evidence
    add("## What the data says")
    for ev in primary:
        if not ev.ok:
            continue
        add(f"\n**{ev.purpose}** [{ev.id}]\n")
        add(ev.markdown_table(12))
    add("")

    # diagnosis
    add("## Why (tested hypotheses)")
    for v in diagnosis.get("verdicts", []):
        icon = {"CONFIRMED": "✔ CONFIRMED", "REJECTED": "✘ REJECTED"}.get(
            v.get("verdict"), "? INCONCLUSIVE")
        ids = " ".join(f"[{i}]" for i in v.get("evidence_ids", []))
        add(f"- **{icon}** — {v.get('statement', '')} {ids}")
        add(f"  {v.get('rationale', '')}")
    add("")

    # recommendations
    add("## Recommended actions (ranked)")
    if actions:
        for i, a in enumerate(actions, 1):
            ids = " ".join(f"[{j}]" for j in a.get("evidence_ids", []))
            add(f"{i}. **{a['action']}** — impact: {a.get('expected_impact')}, "
                f"confidence: {a.get('confidence')} {ids}")
            add(f"   {a.get('rationale', '')}")
    else:
        add("_No actions could be grounded in the evidence._")
    add("")

    # appendix: full evidence
    add("---")
    add("## Appendix — evidence (the exact SQL behind every number)")
    for ev in registry.all():
        status = f"⚠ {ev.error}" if ev.error else ev.trust_line()
        add(f"\n### [{ev.id}] {ev.purpose}\n`{status}`\n")
        add("```sql")
        add(ev.sql)
        add("```")
        if ev.ok:
            add(ev.markdown_table(15))
    add("")
    add(f"---\n*Pipeline: PARSE → QUERY → DIAGNOSE → RECOMMEND → BRIEF · "
        f"queries: {len(registry)} · model: {meta.get('model', 'deterministic fallback')}*")

    return _clean_citations("\n".join(lines), registry)


def slugify(q: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", q.lower()).strip("-")
    return s[:60] or "brief"


def save(brief_text: str, question: str, briefs_dir: str) -> str:
    import pathlib
    p = pathlib.Path(briefs_dir)
    p.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = p / f"{stamp}-{slugify(question)}.md"
    path.write_text(brief_text, encoding="utf-8")
    return str(path)
