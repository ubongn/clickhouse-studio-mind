"""The CI gate itself must be tested: briefs that earn their claims pass,
briefs that don't get flagged."""

from __future__ import annotations

from pathlib import Path

from examples.verify_briefs import verify
from studio_mind.evidence import EvidenceRegistry
from studio_mind.pipeline.brief import build, save


def _make_brief(tmp_path: Path) -> Path:
    registry = EvidenceRegistry()
    ev = registry.add(purpose="audience by episode", sql="SELECT ep, uniqExact(user_id) FROM viewing_events",
                      columns=["ep", "viewers"], rows=[[1, 42], [2, 41], [5, 25]],
                      elapsed_ms=318.0, read_rows=1_234_567, read_size="45.62 MiB",
                      server_ms=245.0)
    registry.add(purpose="completion by episode", sql="SELECT ep, avg(completion_pct) FROM viewing_events",
                 columns=["ep", "avg_comp"], rows=[[1, 0.83], [5, 0.50]], elapsed_ms=290.0)
    text = build(
        question="Why did the show lose audience by episode 5?",
        intent={"analysis_type": "episode_funnel"},
        primary=[ev],
        diagnosis={"verdicts": [{
            "verdict": "CONFIRMED",
            "statement": "Completion falls from ep3 to ep5",
            "rationale": "avg completion halves",
            "evidence_ids": [ev.id],
        }]},
        actions=[{
            "action": "Re-edit the ep4 cold open",
            "expected_impact": "recover ~15% of ep5 drop-off",
            "confidence": "medium",
            "rationale": "the cliff is quality-driven",
            "evidence_ids": [ev.id],
        }],
        registry=registry,
    )
    return Path(save(text, "why did the show lose audience", str(tmp_path)))


def test_sound_brief_passes_the_gate(tmp_path):
    path = _make_brief(tmp_path)
    assert verify(path) == []
    text = path.read_text(encoding="utf-8")
    # the trust receipt (wall · server · rows scanned · bytes) made it in
    assert "rows scanned" in text
    assert "45.62 MiB" in text


def test_brief_without_sql_is_flagged(tmp_path):
    path = _make_brief(tmp_path)
    text = path.read_text(encoding="utf-8").replace("```sql", "~~~sql")
    tampered = tmp_path / "tampered.md"
    tampered.write_text(text, encoding="utf-8")
    problems = verify(tampered)
    assert any("SQL" in p for p in problems)


def test_brief_without_citations_is_flagged(tmp_path):
    path = _make_brief(tmp_path)
    text = path.read_text(encoding="utf-8").replace("[Q", "[Z")
    tampered = tmp_path / "tampered2.md"
    tampered.write_text(text, encoding="utf-8")
    problems = verify(tampered)
    assert any("citation" in p for p in problems)
