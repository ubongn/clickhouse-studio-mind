# Submission draft — ClickHouse Studio Mind

Status: **draft for Ubong's review** · target: submit Sep 6, hack closes
Sep 9 22:00 WAT. Fill `[VIDEO_URL]` after upload (YouTube unlisted or Vercel
static — decide at upload time).

---

## Title

**ClickHouse Studio Mind — decision briefs with SQL receipts**

Alt (shorter form field): `ClickHouse Studio Mind`

## One-line pitch

An AI analyst for streaming-studio executives: plain-English questions in,
evidence-cited decision briefs out — every number links to the exact
ClickHouse query, its scan cost, and a proactive morning brief that catches
what changed overnight.

## Description

Executives don't trust dashboards they can't interrogate, and they don't
trust AI numbers they can't trace. ClickHouse Studio Mind answers
plain-English questions ("Which genres keep viewers past episode 3 in
EMEA?") with a decision brief in which **no number exists without a SQL
receipt** — the query, its plan, the rows it returned, and what the scan
cost (wall ms, server ms, rows read, bytes) straight from ClickHouse's own
`system.query_log`.

Every warehouse call — on-demand asks and the 7am "what changed overnight"
brief alike — goes through the **official `mcp-clickhouse` server**, the
only door to a 50-million-row `viewing_events` dataset on ClickHouse Cloud
(synthetic studio: Nimbus+, Nightfall Division). Planning, diagnosis and
write-up run on **Gemini 2.5 Flash via Vertex AI** on Google Cloud — zero
OpenAI/Anthropic code paths. Each run emits a Langfuse-shaped span tree
(stages → LLM generations → MCP tool calls with scan facts) rendered right
in the UI. The morning brief compares yesterday to the trailing week with
z-scores and a watchlist: in the demo, a CDN-incident rebuffer spike is
flagged and attributed (NA · mobile) before support even notices.

**How it maps to the judging criteria:**

- **Effective use of ClickHouse** — the whole product is a trust layer over
  ClickHouse: MergeTree schema with bloom-filter indices and materialized
  date columns for a 50M-event fact table; `uniqExact` DAU, `countIf` churn
  windows, and QoE rollups computed in-engine; scan receipts sourced from
  `system.query_log` through the same MCP channel; the official
  mcp-clickhouse server as the single warehouse transport.
- **Innovation & creativity** — the evidence-registry pattern (every brief
  claim cites a query ID, clickable to SQL + result + scan cost) applied to
  exec Q&A, plus a proactive anomaly watchlist with device/region
  attribution — "observability for decisions," not another chat-over-SQL
  wrapper.
- **Practical value** — the morning brief and read-only-by-construction
  guard (sqlguard + server-side readonly) make it deployable next to real
  warehouse data on day one; the briefs are written for exec consumption,
  not analysts.
- **Quality of demo & presentation** — hosted, no login, live 50M-row asks
  on camera with the pipeline narrating itself; repo with CI, MIT license,
  file:line "runtime evidence" anchors proving the MCP + Vertex production
  path, and a ≤3:00 demo walking question → trace → receipt → number →
  morning brief.

## Links

| Asset | URL |
|---|---|
| Repo (MIT, CI badge) | https://github.com/ubongn/clickhouse-studio-mind |
| Hosted demo (no login) | https://clickhouse-studio-mind.vercel.app |
| Demo video (≤3:00) | [VIDEO_URL] |
| Architecture (light theme) | `docs/video/architecture.svg` in the repo |

## Built with

- **ClickHouse Cloud** + official **mcp-clickhouse** server (the only
  runtime warehouse path; `clickhouse-connect` is used solely for bulk seed
  loading)
- **Google Cloud**: Cloud Run service, **Gemini 2.5 Flash on Vertex AI**
  (runtime service account / ADC)
- Python (FastAPI), Vercel for the static judge page; zero OpenAI/Anthropic
  code paths; fictional studio data from `data/generate.py --rows 50000000`

## Pre-submit checklist

- [ ] Video ≤ 3:00, English, shows real hosted product (per
      `docs/video/script.md`), `[VIDEO_URL]` filled in above
- [ ] Repo public, LICENSE visible at root, CI badge green
- [ ] Deep health green on hosted URL (`/health?deep=1`)
- [ ] 50M rows loaded (a `/morning` receipt on camera shows the scan count)
- [ ] Morning-brief watchlist non-empty on camera (pin `?date=2026-05-21`
      if the live day is calm)
- [ ] README top has hosted-URL block + "Judges start here"
