import pytest

from studio_mind.evidence import EvidenceRegistry


def test_add_and_get():
    r = EvidenceRegistry()
    ev = r.add("purpose", "SELECT 1", columns=["x"], rows=[[1], [2]], elapsed_ms=5.0)
    assert ev.id == "Q1"
    assert r.get("Q1") is ev
    assert len(r) == 1
    ev2 = r.add("another", "SELECT 2")
    assert ev2.id == "Q2"


def test_require_unknown_raises():
    r = EvidenceRegistry()
    with pytest.raises(KeyError):
        r.require("Q9")


def test_valid_ids_filters():
    r = EvidenceRegistry()
    r.add("p", "SELECT 1")
    assert r.valid_ids(["Q1", "Q2", "Q3"]) == ["Q1"]


def test_markdown_table():
    r = EvidenceRegistry()
    ev = r.add("p", "SELECT 1", columns=["a", "b"], rows=[[1, 0.5], [2, None]])
    md = ev.markdown_table()
    assert "| a | b |" in md
    assert "0.5" in md


def test_failed_evidence_marks_not_ok():
    r = EvidenceRegistry()
    ev = r.add("p", "SELECT broken(", error="syntax error")
    assert not ev.ok
    assert ev.row_count == 0
