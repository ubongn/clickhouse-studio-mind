# Live end-to-end transcript — local server, container-identical runtime path

Date: 2026-08-26. Runtime: official **mcp-clickhouse** (`STUDIO_MIND_TRANSPORT=mcp`)
-> ClickHouse Cloud 26.2.1.558 (germanywestcentral) -> **Gemini 2.5 Flash via
Vertex AI** (`PROVIDER=vertex`, ADC service-account auth). Same code path the
Cloud Run container uses (ADC there = the runtime service account).

## GET /health?deep=1

```json
{"status":"ok","service":"studio-mind","transport":"mcp","database":"studio",
 "provider":"vertex","model":"gemini-2.5-flash",
 "clickhouse":{"ok":true,"version":"26.2.1.558"}}
```

## POST /ask

```http
POST /ask HTTP/1.1   {"question": "Which genres keep viewers past episode 3 in EMEA?"}
```

- transport: mcp  |  provider: vertex  |  model: gemini-2.5-flash
- llm_used: True  |  primary evidence: Q1
- timings_ms: {"parse_ms": 2, "query_ms": 4109, "diagnose_ms": 15800, "recommend_ms": 16582, "brief_ms": 1, "total_ms": 42548}
- intent: {"analysis_type": "segment", "metrics": ["unique_viewers", "avg_completion"], "group_by": "genre", "region": "EMEA", "time_start": "2026-02-01", "time_end": "2026-07-31", "question": "Which genres keep viewers past episode 3 in EMEA?"}

### Evidence Q1 — Segment cut by t.genre: completion, unique viewers, hours

```sql
SELECT t.genre AS segment, uniqExact(v.user_id) AS unique_viewers, round(avg(v.completion_pct), 3) AS avg_completion, round(sum(v.watched_seconds)/3600, 0) AS hours_watched, round(avg(v.ad_impressions), 2) AS avg_ads_per_play FROM viewing_events v INNER JOIN titles t ON t.title_id = v.title_id WHERE 1=1 AND v.region = 'EMEA' AND v.event_date BETWEEN '2026-02-01' AND '2026-07-31' GROUP BY segment ORDER BY unique_viewers DESC LIMIT 10000
```

rows: 11 — first 3: [["thriller", 180560, 0.698, 744715.0, 2.16], ["drama", 177368, 0.704, 842018.0, 2.08], ["animation", 147329, 0.745, 495063.0, 1.48]]

### Stage trace

```
TRACE ask · Which genres keep viewers past episode 3 in EMEA?  [tr-1f3fbce3732f]
  ▸ pipeline                      39399.3 ms
    ▸ stage · parse                     2.4 ms
      ✦ gemini · structured               1.8 ms  ERROR
    ▸ stage · query                  3698.6 ms
      ⇄ mcp-clickhouse · run_query     3483.3 ms
    ▸ stage · diagnose              15800.0 ms
      ✦ gemini · structured           15799.9 ms  ·  2,746 tok
    ▸ stage · recommend             16581.5 ms
      ✦ gemini · structured           16581.4 ms  ·  3,601 tok
    ▸ stage · brief                     1.4 ms
```

### Brief (opening)

```markdown
# Decision brief — Which genres keep viewers past episode 3 in EMEA?
*Generated 2026-08-26 13:44 UTC · ClickHouse Studio Mind · every number cites its query ([Qn] markers resolve in the appendix)*

## What the data says

**Segment cut by t.genre: completion, unique viewers, hours** [Q1]

| segment | unique_viewers | avg_completion | hours_watched | avg_ads_per_play |
|---|---|---|---|---|
| thriller | 180560 | 0.698 | 7.447e+05 | 2.16 |
| drama | 177368 | 0.704 | 8.42e+05 | 2.08 |
| animation | 147329 | 0.745 | 4.951e+05 | 1.48 |
| crime | 142942 | 0.721 | 5.451e+05 | 2.12 |
| comedy | 135864 | 0.726 | 4.028e+05 | 1.65 |
| documentary | 134401 | 0.734 | 4.544e+05 | 2.21 |
| reality | 114915 | 0.731 | 3.409e+05 | 2.05 |
| scifi | 96548 | 0.784 | 3.543e+05 | 1.71 |
| fantasy | 96370 | 0.738 | 3.632e+05 | 2.03 |
| romance | 88733 | 0.707 | 2.386e+05 | 2.24 |
| horror | 67280 | 0.733 | 1.696
```
