"""Pipeline orchestrator: PARSE → QUERY → DIAGNOSE → RECOMMEND → BRIEF."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .. import ch
from ..config import Settings, get_settings
from ..evidence import EvidenceRegistry
from ..llm import LLM, LLMError
from .. import tracing
from . import brief as brief_stage
from . import diagnose as diagnose_stage
from . import parse as parse_stage
from . import query as query_stage
from . import recommend as recommend_stage


@dataclass
class RunResult:
    question: str
    intent: dict
    brief: str = ""
    brief_path: str = ""
    primary_ids: list[str] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    registry_json: str = ""
    trace_json: str = ""
    trace_tree: str = ""
    llm_used: bool = False


def run_pipeline(question: str, settings: Settings | None = None,
                 use_llm: bool = True) -> RunResult:
    s = settings or get_settings()
    t_start = time.perf_counter()
    timings: dict[str, float] = {}

    collector = tracing.TraceCollector(
        name=f"ask · {question[:60]}",
        metadata={"transport": s.ch.transport, "database": s.ch.database},
    )
    token = tracing.set_active_collector(collector)

    client = ch.get_client(s)
    try:
        with collector.span("pipeline", input_=question):
            result = _run_with_client(question, s, client, use_llm,
                                      t_start, timings, collector)
        result.trace_json = collector.to_json()
        result.trace_tree = collector.tree()
        _save_trace(collector, result.brief_path)
        return result
    finally:
        tracing.reset_active_collector(token)
        ch.close_client(client)


def _save_trace(collector: tracing.TraceCollector, brief_path: str) -> None:
    if not brief_path:
        return
    from pathlib import Path

    p = Path(brief_path)
    trace_path = p.with_name(p.stem + ".trace.json")
    trace_path.write_text(collector.to_json(), encoding="utf-8")


def _run_with_client(question: str, s: Settings, client,
                     use_llm: bool, t_start: float, timings: dict,
                     collector: tracing.TraceCollector) -> RunResult:
    registry = EvidenceRegistry()

    llm: LLM | None = None
    if use_llm:
        llm = LLM(s)
        try:
            _ = llm.client.models  # force auth path; raises if misconfigured
        except LLMError:
            llm = None
        except Exception:
            llm = None

    # titles glossary (deterministic entity binding)
    try:
        titles = client.query(
            f"SELECT title_id, title_name FROM {s.ch_database}.titles "
            f"ORDER BY popularity DESC"
        ).result_rows
    except Exception:
        titles = []

    # 1. PARSE --------------------------------------------------------------
    t0 = time.perf_counter()
    with collector.span("stage · parse", input_=question):
        intent = parse_stage.parse(question, llm=llm, titles=titles)
    intent["question"] = question
    timings["parse_ms"] = (time.perf_counter() - t0) * 1000

    # 2. QUERY --------------------------------------------------------------
    t0 = time.perf_counter()
    schema_brief = ""
    if llm is not None:
        try:
            schema_brief = ch.schema_context(client, s.ch_database)
        except Exception:
            schema_brief = ""
    with collector.span("stage · query", input_=intent):
        primary, source = query_stage.run(client, registry, intent, question,
                                          llm=llm, schema_brief=schema_brief)
    timings["query_ms"] = (time.perf_counter() - t0) * 1000
    if not primary:
        raise RuntimeError(
            "QUERY stage produced no usable evidence — check ClickHouse connection "
            "and that the dataset is loaded (python -m data.generate)"
        )

    # 3. DIAGNOSE -----------------------------------------------------------
    t0 = time.perf_counter()
    with collector.span("stage · diagnose"):
        diagnosis = diagnose_stage.run(client, registry, intent, primary, llm=llm)
    timings["diagnose_ms"] = (time.perf_counter() - t0) * 1000

    # 4. RECOMMEND ----------------------------------------------------------
    t0 = time.perf_counter()
    with collector.span("stage · recommend"):
        actions = recommend_stage.run(registry, intent, primary, diagnosis, llm=llm)
    timings["recommend_ms"] = (time.perf_counter() - t0) * 1000

    # 5. BRIEF --------------------------------------------------------------
    t0 = time.perf_counter()
    meta = {"model": llm.model if llm else "deterministic fallback",
            "query_source": source}
    with collector.span("stage · brief"):
        text = brief_stage.build(question, intent, primary, diagnosis, actions,
                                 registry, meta=meta)
        path = brief_stage.save(text, question, s.briefs_dir)
    timings["brief_ms"] = (time.perf_counter() - t0) * 1000
    timings["total_ms"] = (time.perf_counter() - t_start) * 1000

    return RunResult(
        question=question, intent=intent, brief=text, brief_path=path,
        primary_ids=[e.id for e in primary],
        timings=timings, registry_json=registry.to_json(),
        llm_used=llm is not None,
    )
