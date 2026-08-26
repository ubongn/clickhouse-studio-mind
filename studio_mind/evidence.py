"""Evidence registry — the zero-hallucination backbone.

Every query the pipeline runs becomes an Evidence object with a stable ID
(Q1, Q2, …). Every number that appears in a brief must reference one of these
IDs; the brief builder physically cannot cite anything else.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Evidence:
    id: str
    purpose: str                 # why this query exists (stage / hypothesis)
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    elapsed_ms: float = 0.0
    plan: str | None = None      # ClickHouse EXPLAIN (shape transparency)
    error: str | None = None
    # trust panel — server-side facts about the scan (MCP transport)
    read_rows: int | None = None    # rows ClickHouse actually read
    read_size: str | None = None    # bytes read, human-readable
    server_ms: float | None = None  # server-side query duration

    @property
    def ok(self) -> bool:
        return self.error is None

    def trust_line(self) -> str:
        """One-line scan receipt for the trust panel / brief footer."""
        bits = [f"{self.elapsed_ms:.0f} ms wall"]
        if self.server_ms is not None:
            bits.append(f"{self.server_ms:.0f} ms server")
        if self.read_rows is not None:
            bits.append(f"{self.read_rows:,} rows scanned")
        if self.read_size:
            bits.append(self.read_size)
        return " · ".join(bits)

    def head(self, n: int = 8) -> list[list[Any]]:
        return self.rows[:n]

    def markdown_table(self, n: int = 15) -> str:
        if not self.columns:
            return "_(no result — query failed)_"
        cols = self.columns
        lines = ["| " + " | ".join(str(c) for c in cols) + " |",
                 "|" + "|".join(["---"] * len(cols)) + "|"]
        for r in self.rows[:n]:
            lines.append("| " + " | ".join(_fmt(v) for v in r) + " |")
        if len(self.rows) > n:
            lines.append(f"| … {len(self.rows) - n} more rows |")
        return "\n".join(lines)


def _fmt(v: Any) -> str:
    if v is None:
        return "∅"
    if isinstance(v, float):
        return f"{v:,.4g}"
    return str(v)


class EvidenceRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}
        self._n = 0

    def add(self, purpose: str, sql: str, *, columns: list[str] | None = None,
            rows: list[list[Any]] | None = None, elapsed_ms: float = 0.0,
            plan: str | None = None, error: str | None = None,
            read_rows: int | None = None, read_size: str | None = None,
            server_ms: float | None = None) -> Evidence:
        self._n += 1
        ev = Evidence(
            id=f"Q{self._n}", purpose=purpose, sql=sql.strip(),
            columns=columns or [], rows=rows or [],
            row_count=len(rows or []), elapsed_ms=round(elapsed_ms, 1),
            plan=plan, error=error,
            read_rows=read_rows, read_size=read_size, server_ms=server_ms,
        )
        self._items[ev.id] = ev
        return ev

    def get(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def require(self, evidence_id: str) -> Evidence:
        ev = self._items.get(evidence_id)
        if ev is None:
            raise KeyError(f"unknown evidence id {evidence_id!r} — refusing to cite it")
        return ev

    def all(self) -> list[Evidence]:
        return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def valid_ids(self, ids: list[str]) -> list[str]:
        """Filter a list of claimed evidence IDs down to ones that exist."""
        return [i for i in ids if i in self._items]

    def to_json(self) -> str:
        return json.dumps([asdict(e) for e in self._items.values()], indent=1, default=str)
