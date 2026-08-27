"""Local UX harness — serves the real judge page with a slow, fake /ask.

The production /ask takes 45-90s (cold ClickHouse Cloud + Vertex + MCP).
This stub serves the exact HTML from studio_mind.server but swaps the
pipeline for a canned RunResult behind a sleep, so the waiting UX can be
exercised/photographed offline with zero credentials:

    STUB_DELAY=75 python scripts/stub_server.py     # default port 8099

Then open http://127.0.0.1:8099, ask anything, watch the waiting states.
"""

from __future__ import annotations

import json
import os
import time

import uvicorn

import studio_mind.server as srv
from studio_mind.pipeline.run import RunResult

DELAY = float(os.environ.get("STUB_DELAY", "75"))
PORT = int(os.environ.get("PORT", "8099"))

_BRIEF = """Drama and crime/thriller are what keep EMEA viewers past episode 3.

**What the data says (studio.events, 30d)**
- Drama retains 82% of its episode-1 cohort at episode 3 — the highest of any genre (Q1).
- Crime/thriller is close behind at 79%, driven by binge-style weekend viewing (Q1).
- Reality drops to 41%, the steepest fall-off; it acquires well but decays fast (Q1).
- The drama lead is consistent across the EMEA sub-regions, not a UK-only artifact (Q2).

**What to do**
- Greenlight drama and crime/thriller slates for the EMEA spring window; they carry retention, not just launch buzz.
- Treat reality as acquisition-only: pair it with a drama/thriller recommendation module to catch the fall-off.
- Watch episode 2→3 as the decision point — that is where reality bleeds and drama compounds.
"""

_REGISTRY = json.dumps([
    {
        "id": "Q1", "purpose": "retention to episode 3 by genre (EMEA, 30d)",
        "sql": "SELECT genre, round(countIf(event='watch' AND episode=3) "
               "/ nullIf(countIf(event='watch' AND episode=1), 0), 2) AS retention "
               "FROM studio.events WHERE region='EMEA' GROUP BY genre ORDER BY retention DESC",
        "columns": ["genre", "retention"], "rows": [["drama", 0.82], ["crime", 0.79], ["reality", 0.41]],
    },
    {
        "id": "Q2", "purpose": "drama retention by EMEA sub-region",
        "sql": "SELECT sub_region, retention FROM studio.events "
               "WHERE genre='drama' GROUP BY sub_region",
        "columns": ["sub_region", "retention"], "rows": [["UK", 0.83], ["DACH", 0.81], ["Nordics", 0.80]],
    },
])


def fake_run(question, settings=None, use_llm=True):
    time.sleep(DELAY)
    return RunResult(
        question=question,
        intent={"kind": "segment", "dimension": "genre", "region": "EMEA"},
        brief=_BRIEF,
        primary_ids=["Q1", "Q2"],
        timings={"parse_ms": 2.0, "query_ms": 4100.0, "diagnose_ms": 15800.0,
                 "recommend_ms": 16600.0, "total_ms": 36500.0},
        registry_json=_REGISTRY,
        trace_json="{}",
        trace_tree="ask\n ├─ parse\n ├─ query (mcp-clickhouse)\n ├─ diagnose\n └─ recommend",
        llm_used=True,
    )


srv.run_pipeline = fake_run  # the /ask route resolves this name at call time

app = srv.app

if __name__ == "__main__":
    print(f"stub /ask delay={DELAY}s on http://127.0.0.1:{PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
