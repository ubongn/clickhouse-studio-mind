"""Morning brief — "what changed overnight" against the viewing warehouse.

The proactive half of Studio Mind: no question needed. Every morning this
lands in the exec's inbox: yesterday's key metrics vs the trailing 7-day
baseline, a watchlist of statistically unusual moves (z-score >= 2), and
device/region attribution for QoE spikes — every number backed by the same
SQL-receipt + trust-panel machinery as /ask (latency, rows scanned, evidence
ids). Deterministic by design: the LLM writes briefs on demand, but the 7am
push never depends on a model being up.

CLI:  python -m studio_mind.morning [--date 2026-05-21]
API:  GET /morning[?date=...]
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from . import ch, tracing
from .evidence import EvidenceRegistry

# metrics pulled into the daily series, with human labels + higher-is-better
METRICS = [
    ("events", "Viewing events", "higher"),
    ("dau", "Daily active viewers", "higher"),
    ("avg_completion", "Avg completion", "higher"),
    ("rebuffer_per_event", "Rebuffer s/event", "lower"),
    ("ad_seconds_per_event", "Ad load s/event", "lower"),
]

_DATE_OK = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class MorningResult:
    date: str
    metrics: list[dict] = field(default_factory=list)
    watchlist: list[dict] = field(default_factory=list)
    attribution: list[dict] = field(default_factory=list)
    churn: dict = field(default_factory=dict)
    brief_md: str = ""
    registry: EvidenceRegistry | None = None


# ---------------------------------------------------------------- queries ---


def _series_sql(db: str, anchor: str) -> str:
    return (
        f"SELECT toDate(event_time) AS day, count() AS events, "
        f"uniqExact(user_id) AS dau, round(avg(completion_pct), 4) AS avg_completion, "
        f"round(sum(rebuffer_seconds) / greatest(count(), 1), 3) AS rebuffer_per_event, "
        f"round(avg(ad_seconds), 1) AS ad_seconds_per_event "
        f"FROM {db}.viewing_events "
        f"WHERE toDate(event_time) >= toDate('{anchor}') - INTERVAL 8 DAY "
        f"AND toDate(event_time) <= toDate('{anchor}') "
        f"GROUP BY day ORDER BY day"
    )


def _device_sql(db: str, anchor: str) -> str:
    return (
        f"SELECT device, region, count() AS events, "
        f"round(sum(rebuffer_seconds) / greatest(count(), 1), 3) AS rebuffer_per_event "
        f"FROM {db}.viewing_events "
        f"WHERE toDate(event_time) = toDate('{anchor}') "
        f"GROUP BY device, region ORDER BY rebuffer_per_event DESC"
    )


def _churn_sql(db: str, anchor: str) -> str:
    return (
        f"SELECT countIf(churn_date = toDate('{anchor}')) AS churned_today, "
        f"round(countIf(churn_date >= toDate('{anchor}') - INTERVAL 7 DAY "
        f"AND churn_date < toDate('{anchor}')) / 7.0, 1) AS churn_baseline_per_day "
        f"FROM {db}.users"
    )


# ------------------------------------------------------------------ core ---


def _z(value: float, base: list[float]) -> float:
    if len(base) < 3:
        return 0.0
    sd = statistics.pstdev(base)
    if sd < 1e-9:
        return 0.0
    return (value - statistics.fmean(base)) / sd


def build_morning(client, registry: EvidenceRegistry, database: str,
                  date: str | None = None) -> MorningResult:
    """Run the receipts and assemble the morning result (all through MCP)."""
    # Q0 — anchor on the latest day present in the warehouse (demo data is
    # historical; "overnight" = the newest day of data unless pinned)
    if date is not None:
        if not _DATE_OK.match(date):
            raise ValueError("date must be YYYY-MM-DD")
        anchor = date
    else:
        q0 = ch.run_query(
            client, registry, "anchor: latest event day",
            f"SELECT toString(max(toDate(event_time))) FROM {database}.viewing_events")
        anchor = str(q0.rows[0][0]) if q0.rows and q0.ok else None
        if anchor is None:
            raise RuntimeError("no viewing data — cannot build a morning brief")

    # Q1 — daily series ending on the anchor
    series_ev = ch.run_query(client, registry, "daily metrics (9-day series)",
                             _series_sql(database, anchor))
    cols = list(series_ev.columns)
    days = [dict(zip(cols, [str(v)[:10] if i == 0 else v for i, v in enumerate(r)]))
            for r in series_ev.rows]
    if len(days) < 2:
        raise RuntimeError("not enough history for a baseline")

    today = days[-1]
    base = days[-8:-1] if len(days) >= 8 else days[:-1]

    metrics = []
    for key, label, good in METRICS:
        v = float(today[key])
        b = [float(d[key]) for d in base]
        mean = statistics.fmean(b)
        z = _z(v, b)
        pct = (v - mean) / mean * 100 if abs(mean) > 1e-9 else 0.0
        metrics.append({
            "key": key, "label": label, "good": good,
            "value": v, "baseline": round(mean, 4),
            "delta": round(v - mean, 4), "pct": round(pct, 1), "z": round(z, 2),
        })

    # watchlist: unusual moves only (z >= 2 against direction of good, or
    # >20% off a quiet baseline)
    watchlist = []
    for m in metrics:
        bad_dir = m["z"] <= -2 if m["good"] == "higher" else m["z"] >= 2
        if bad_dir and abs(m["pct"]) >= 8:
            lvl = "high" if abs(m["z"]) >= 3 else "medium"
            watchlist.append({
                "level": lvl, "metric": m["label"], "z": m["z"], "pct": m["pct"],
                "detail": (f"{m['label']} {m['value']} vs baseline "
                           f"{m['baseline']} ({m['pct']:+.1f}%, z={m['z']:+.2f})"),
            })
    watchlist.sort(key=lambda w: -abs(w["z"]))

    # Q2 — attribution: which device/region carries a rebuffer spike
    # (threshold = 3x the *baseline* rebuffer, not the incident day's own mean)
    attribution = []
    baseline_rb = next((m["baseline"] for m in metrics
                        if m["key"] == "rebuffer_per_event"), 0.0)
    dev_ev = ch.run_query(client, registry, "rebuffer attribution (device x region)",
                          _device_sql(database, anchor))
    if dev_ev.ok:
        dcols = list(dev_ev.columns)
        for r in dev_ev.rows:
            row = dict(zip(dcols, r))
            rb = float(row["rebuffer_per_event"])
            if rb > max(0.25, 3 * baseline_rb):
                attribution.append({
                    "device": str(row["device"]), "region": str(row["region"]),
                    "rebuffer_per_event": rb,
                    "events": int(row["events"]),
                    "detail": (f"{row['region']} · {row['device']}: "
                               f"{rb:.2f}s rebuffer/event over "
                               f"{int(row['events']):,} events "
                               f"(week baseline {baseline_rb:.2f}s)"),
                })
        attribution = attribution[:3]

    # Q3 — churn dates landing today
    churn = {"today": 0, "baseline_per_day": 0.0}
    ch_ev = ch.run_query(client, registry, "churn dates landing today",
                         _churn_sql(database, anchor))
    if ch_ev.ok and ch_ev.rows:
        churn = {"today": int(ch_ev.rows[0][0]),
                 "baseline_per_day": float(ch_ev.rows[0][1] or 0.0)}
        cz = _z(churn["today"], [churn["baseline_per_day"]] * 7)  # vs flat base
        if churn["today"] >= 1.3 * churn["baseline_per_day"] and churn["baseline_per_day"] > 0:
            watchlist.append({
                "level": "medium", "metric": "Churn",
                "z": round(cz, 2), "pct": round(
                    (churn["today"] - churn["baseline_per_day"])
                    / churn["baseline_per_day"] * 100, 1),
                "detail": (f"{churn['today']} churn dates today vs "
                           f"{churn['baseline_per_day']:.0f}/day trailing week"),
            })

    res = MorningResult(date=anchor, metrics=metrics, watchlist=watchlist,
                        attribution=attribution, churn=churn, registry=registry)
    res.brief_md = _render(res)
    return res


# ---------------------------------------------------------------- render ---


def _fmt(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _render(r: MorningResult) -> str:
    sev = {"high": "[HIGH]", "medium": "[MED]"}
    lines = [
        f"# Morning Brief — {r.date} · Nimbus+ Studio Ops",
        "",
        "## What changed overnight",
        "",
        "| metric | yesterday | 7-day baseline | Δ | z |",
        "|---|---:|---:|---:|---:|",
    ]
    for m in r.metrics:
        lines.append(
            f"| {m['label']} | {_fmt(m['value'])} | {_fmt(m['baseline'])} "
            f"| {m['pct']:+.1f}% | {m['z']:+.2f} |")
    lines += ["", f"**Churn dates landing today:** {r.churn['today']} "
              f"(trailing week: {r.churn['baseline_per_day']:.0f}/day)", ""]

    if r.watchlist:
        lines += ["## Watchlist", ""]
        for w in r.watchlist:
            lines.append(f"- {sev.get(w['level'], '[NOTE]')} **{w['metric']}** — {w['detail']}")
    else:
        lines += ["## Watchlist", "", "- Nothing unusual overnight. Ship it."]

    if r.attribution:
        lines += ["", "## QoE attribution", ""]
        lines += [f"- {a['detail']}" for a in r.attribution]

    n_ev = len(r.registry.all()) if r.registry else 0
    lines += [
        "", "---",
        f"*Every number above comes from one of {n_ev} SQL receipts "
        "(Q0–Q3) — each with latency and rows-scanned in the trust panel. "
        "Same official mcp-clickhouse transport as every /ask.*",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------- CLI ---


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .config import get_settings

    ap = argparse.ArgumentParser(description="Nimbus+ morning brief")
    ap.add_argument("--date", default=None, help="anchor day, YYYY-MM-DD")
    args = ap.parse_args(argv)

    s = get_settings()
    registry = EvidenceRegistry()
    collector = tracing.TraceCollector(
        f"morning · {args.date or 'latest'}",
        metadata={"transport": s.ch.transport, "database": s.ch.database})
    token = tracing.set_active_collector(collector)
    client = ch.get_client(s)
    try:
        with collector.span("morning brief"):
            r = build_morning(client, registry, s.ch.database, args.date)
        print(r.brief_md)
        print("\n" + collector.tree())
        return 0
    finally:
        tracing.reset_active_collector(token)
        ch.close_client(client)


if __name__ == "__main__":
    raise SystemExit(main())
