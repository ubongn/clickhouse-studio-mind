"""Langfuse-style tracing for every pipeline run.

Observation model mirrors Langfuse: a TRACE owns a tree of OBSERVATIONS of
three kinds — ``span`` (pipeline stages), ``generation`` (LLM calls, with
model + token usage) and ``tool`` (MCP tool calls, with SQL + scan facts).
Every observation carries wall latency; tool observations carry the trust
facts (rows scanned, server ms) so the span tree in the console IS the
receipt for every number in the brief.

No external service required — traces serialize to JSON (saved next to each
brief) and render as an ASCII tree for the CLI. The shape is deliberately
Langfuse-compatible (trace/observation/level/usage) so exporting to a
Langfuse OSS instance later is a mapping, not a rewrite.
"""

from __future__ import annotations

import contextvars
import itertools
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

_ids = itertools.count(1)


def _short_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass
class Observation:
    id: str
    trace_id: str
    parent_id: str | None
    type: str                  # span | generation | tool
    name: str
    start_time: float          # unix seconds, perf_clock-anchored via trace
    end_time: float | None = None
    level: str = "DEFAULT"     # DEFAULT | ERROR  (Langfuse levels)
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)      # tokens {input, output, total}
    model: str | None = None

    @property
    def latency_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return round((self.end_time - self.start_time) * 1000, 1)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["latency_ms"] = self.latency_ms
        return d


@dataclass
class Trace:
    id: str
    name: str
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TraceCollector:
    """Collects one trace + its observations. Thread-safe enough for the
    pipeline (single-threaded per run); nested spans auto-attach to the
    current span via contextvar."""

    def __init__(self, name: str, metadata: dict[str, Any] | None = None):
        import datetime as dt

        self.trace = Trace(
            id=_short_id("tr"), name=name,
            timestamp=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            metadata=metadata or {},
        )
        self.observations: list[Observation] = []
        self._current: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"span:{self.trace.id}", default=None)

    # -- construction --------------------------------------------------------

    def start(self, type_: str, name: str, input_: Any = None,
              metadata: dict[str, Any] | None = None,
              model: str | None = None) -> Observation:
        obs = Observation(
            id=f"O{next(_ids)}", trace_id=self.trace.id,
            parent_id=self._current.get(), type=type_, name=name,
            start_time=time.perf_counter(), input=_clip(input_),
            metadata=metadata or {}, model=model,
        )
        self.observations.append(obs)
        return obs

    def end(self, obs: Observation, output: Any = None, error: bool = False,
            usage: dict[str, int] | None = None,
            extra_meta: dict[str, Any] | None = None) -> Observation:
        obs.end_time = time.perf_counter()
        obs.output = _clip(output)
        if error:
            obs.level = "ERROR"
        if usage:
            obs.usage = usage
        if extra_meta:
            obs.metadata.update(extra_meta)
        return obs

    # -- context managers (auto-nesting) ---------------------------------------

    def span(self, name: str, type_: str = "span", input_: Any = None,
             metadata: dict[str, Any] | None = None):
        return _SpanCtx(self, name, type_, input_, metadata)

    def tool_span(self, name: str, sql: str):
        return self.span(name, type_="tool", input_=sql,
                         metadata={"transport": "mcp"})

    def llm_span(self, name: str, model: str, input_: Any):
        return self.span(name, type_="generation", input_=input_,
                         metadata={"model": model})

    # -- output -----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace": self.trace.to_dict(),
            "observations": [o.to_dict() for o in self.observations],
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def tree(self) -> str:
        """ASCII span tree with ms — the console/UI sketch of the trace."""
        by_parent: dict[str | None, list[Observation]] = {}
        for o in self.observations:
            by_parent.setdefault(o.parent_id, []).append(o)

        lines = [f"TRACE {self.trace.name}  [{self.trace.id}]"]

        def walk(parent: str | None, depth: int) -> None:
            for o in by_parent.get(parent, []):
                icon = {"span": "▸", "generation": "✦", "tool": "⇄"}.get(o.type, "·")
                lat = f"{o.latency_ms:8.1f} ms" if o.latency_ms is not None else "     …"
                err = "  ERROR" if o.level == "ERROR" else ""
                tok = ""
                if o.type == "generation" and o.usage.get("total"):
                    tok = f"  ·  {o.usage.get('total', 0):,} tok"
                rows = ""
                if o.type == "tool" and o.metadata.get("read_rows") is not None:
                    rows = f"  ·  {o.metadata['read_rows']:,} rows scanned"
                lines.append(f"{'  ' * depth}{icon} {o.name:<28} {lat}{tok}{rows}{err}")
                walk(o.id, depth + 1)

        walk(None, 1)
        return "\n".join(lines)

    def totals(self) -> dict[str, Any]:
        llm_obs = [o for o in self.observations if o.type == "generation"]
        tool_obs = [o for o in self.observations if o.type == "tool"]
        return {
            "observations": len(self.observations),
            "llm_calls": len(llm_obs),
            "tool_calls": len(tool_obs),
            "tokens_total": sum(o.usage.get("total", 0) for o in llm_obs),
            "rows_scanned_total": sum(
                o.metadata.get("read_rows") or 0 for o in tool_obs),
            "errors": sum(1 for o in self.observations if o.level == "ERROR"),
        }


class _SpanCtx:
    """Context manager: starts an observation, nests children under it."""

    def __init__(self, collector: TraceCollector, name: str, type_: str,
                 input_: Any, metadata: dict[str, Any] | None):
        self.c = collector
        self.name, self.type, self.input, self.metadata = name, type_, input_, metadata
        self.obs: Observation | None = None
        self._token = None

    def __enter__(self) -> Observation:
        self.obs = self.c.start(self.type, self.name, self.input, self.metadata)
        self._token = self.c._current.set(self.obs.id)
        return self.obs

    def __exit__(self, exc_type, exc, tb) -> bool:
        out = exc if exc is not None else (self.obs.output if self.obs else None)
        if exc is not None and self.obs is not None and self.obs.output is None:
            out = f"{type(exc).__name__}: {exc}"
        if self.obs is not None:
            self.c.end(self.obs, output=out, error=exc is not None)
        if self._token is not None:
            self.c._current.reset(self._token)
        return False  # never swallow


def _clip(value: Any, limit: int = 2000) -> Any:
    """Keep span payloads readable — clip long strings/dicts."""
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 12] + f"…(+{len(value) - limit + 12} chars)"
    return value


# -- active-collector plumbing -------------------------------------------------
# The pipeline sets one active collector per run; ch.run_query and llm.* emit
# nested observations into it without needing the collector threaded through
# every signature.

_active: contextvars.ContextVar["TraceCollector | None"] = contextvars.ContextVar(
    "studio_mind_active_trace", default=None
)


def set_active_collector(collector: TraceCollector | None):
    return _active.set(collector)


def reset_active_collector(token) -> None:
    _active.reset(token)


def active_collector() -> TraceCollector | None:
    return _active.get()


class _NullSpan:
    """Duck-types an Observation for 'no collector active' call sites."""

    metadata: dict[str, Any] = {}

    def __exit__(self, *exc) -> bool:
        return False

    def __enter__(self):
        return self


def maybe_tool_span(name: str, sql: str):
    """Tool span in the active collector, or a no-op context."""
    c = active_collector()
    if c is None:
        return _NullSpan()
    return c.tool_span(name, sql)


def maybe_llm_span(name: str, model: str, input_: Any):
    c = active_collector()
    if c is None:
        return _NullSpan()
    return c.llm_span(name, model, input_)


def iter_trace_files(briefs_dir) -> Iterator[dict[str, Any]]:
    """Yield parsed .trace.json files from a briefs directory (newest first)."""
    import pathlib

    p = pathlib.Path(briefs_dir)
    if not p.exists():
        return
    for f in sorted(p.glob("*.trace.json"), reverse=True):
        try:
            yield json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
