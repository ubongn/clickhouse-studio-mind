"""Morning brief tests — watchlist math, attribution, rendering (no live CH)."""

from __future__ import annotations

import pytest

from studio_mind import morning
from studio_mind.evidence import EvidenceRegistry

# 9 days of a healthy stream, then the CDN incident hits the anchor day:
# rebuffer explodes, completion dips, events sag. The brief must scream.
DAYS = [
    ("2026-05-14", 51_000, 20_100, 0.61, 0.22, 9.1),
    ("2026-05-15", 49_800, 19_700, 0.60, 0.20, 9.0),
    ("2026-05-16", 50_600, 20_000, 0.61, 0.24, 9.2),
    ("2026-05-17", 50_200, 19_900, 0.60, 0.21, 9.1),
    ("2026-05-18", 50_900, 20_200, 0.61, 0.23, 9.0),
    ("2026-05-19", 50_400, 20_000, 0.60, 0.22, 9.1),
    ("2026-05-20", 50_700, 20_100, 0.60, 0.25, 9.2),   # baseline day 7
    ("2026-05-21", 44_900, 18_400, 0.55, 2.10, 9.1),   # ANCHOR — incident
    # 9th day unused by an anchored build (series caps at anchor)
]
BASELINE_END = 7


class FakeMorningClient:
    """Duck-typed MCP-shaped client serving the incident scenario."""

    def __init__(self):
        self.calls: list[str] = []

    def query(self, sql: str):
        self.calls.append(sql)
        assert sql.strip().upper().startswith(("SELECT", "WITH")), sql

        class R:
            column_names: list[str]
            result_rows: list[tuple]
        r = R()
        if "max(toDate(event_time))" in sql:
            r.column_names = ["max_day"]
            r.result_rows = [("2026-05-21",)]
        elif "uniqExact(user_id)" in sql:
            r.column_names = ["day", "events", "dau", "avg_completion",
                              "rebuffer_per_event", "ad_seconds_per_event"]
            r.result_rows = [tuple(DAYS[i]) for i in range(BASELINE_END + 1)]
        elif "GROUP BY device, region" in sql:
            r.column_names = ["device", "region", "events", "rebuffer_per_event"]
            r.result_rows = [
                ("mobile", "NA", 12_400, 5.9),
                ("desktop", "NA", 9_100, 2.8),
                ("tv", "NA", 11_000, 0.1),
                ("mobile", "EMEA", 8_200, 0.2),
            ]
        elif "churned_today" in sql:
            r.column_names = ["churned_today", "churn_baseline_per_day"]
            r.result_rows = [(214, 96.0)]
        else:
            r.column_names = ["n"]
            r.result_rows = [(1,)]
        return r

    def query_stats(self):
        return {"read_rows": 50_000_000, "read_size": "180.00 MiB",
                "query_duration_ms": 245}


def _build():
    return morning.build_morning(FakeMorningClient(), EvidenceRegistry(),
                                 "studio", "2026-05-21")


def test_watchlist_catches_the_cdn_incident():
    r = _build()
    names = " | ".join(w["metric"] for w in r.watchlist)
    assert "Rebuffer" in names
    assert "completion" in names.lower()
    top = r.watchlist[0]
    assert top["metric"] == "Rebuffer s/event"
    assert top["level"] == "high" and top["z"] >= 3


def test_metrics_table_has_baseline_math():
    r = _build()
    m = {x["key"]: x for x in r.metrics}
    # rebuffer: 2.10 vs mean of 7 baseline days ~0.225 → huge z
    assert m["rebuffer_per_event"]["baseline"] == pytest.approx(0.224, abs=0.01)
    assert m["rebuffer_per_event"]["value"] == 2.10
    assert m["dau"]["pct"] == pytest.approx((18_400 - 20_000) / 20_000 * 100, abs=0.5)


def test_attribution_names_na_mobile_first():
    r = _build()
    assert r.attribution, "incident rows must produce attribution"
    assert r.attribution[0]["device"] == "mobile"
    assert r.attribution[0]["region"] == "NA"
    assert r.attribution[0]["rebuffer_per_event"] == 5.9
    # tv/EMEA rows below the 3x threshold are filtered out
    assert not any(a["region"] != "NA" for a in r.attribution)


def test_brief_renders_with_sql_receipt_count():
    r = _build()
    assert "# Morning Brief — 2026-05-21" in r.brief_md
    assert "Watchlist" in r.brief_md
    assert "5.9" in r.brief_md
    # Q0 skipped (date pinned), so: series + device + churn = 3 receipts
    assert "3 SQL receipts" in r.brief_md
    assert len(r.registry.all()) == 3
    assert all(e.ok for e in r.registry.all())


def test_every_query_has_trust_facts():
    r = _build()
    for e in r.registry.all():
        assert e.read_rows == 50_000_000
        assert "rows scanned" in e.trust_line()


def test_bad_date_rejected():
    with pytest.raises(ValueError):
        morning.build_morning(FakeMorningClient(), EvidenceRegistry(),
                              "studio", "21-05-2026; DROP TABLE users")


def test_calm_day_yields_clean_watchlist():
    calm = [d for d in DAYS]
    calm[-1] = ("2026-05-21", 50_500, 20_050, 0.60, 0.22, 9.1)  # nothing changed

    class CalmClient(FakeMorningClient):
        def query(self, sql):
            res = super().query(sql)
            if "uniqExact" in sql:
                res.result_rows = [tuple(calm[i]) for i in range(BASELINE_END + 1)]
            if "GROUP BY device, region" in sql:
                res.result_rows = [("tv", "NA", 11_000, 0.1)]
            if "churned_today" in sql:
                res.result_rows = [(96, 96.0)]
            return res

    r = morning.build_morning(CalmClient(), EvidenceRegistry(), "studio", "2026-05-21")
    assert r.watchlist == []
    assert "Nothing unusual overnight" in r.brief_md


def test_anchor_discovery_when_date_not_pinned():
    r = morning.build_morning(FakeMorningClient(), EvidenceRegistry(), "studio")
    assert r.date == "2026-05-21"
    # Q0 (anchor) + Q1 + Q2 + Q3 = 4 receipts
    assert len(r.registry.all()) == 4
