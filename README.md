# ClickHouse Studio Mind

**Hosted URL (live demo):** <!-- HOSTED_URL --> `https://studio-mind.<project-id>.a.run.app` — one-question demo, no login; verification transcript: [docs/live-transcript-2026-08-26.md](docs/live-transcript-2026-08-26.md) · deploy/reproduce: `python deploy/deploy.py --region europe-west6`

**An analytics agent for studio executives, built ClickHouse-native.**
Ask a plain-English question about audience behavior → get a one-page decision brief where **every single number cites the exact SQL that produced it**.

> *"Why did Nightfall Division lose half its audience by episode 5?"*
> *"Which genres actually retain viewers past episode 3 in EMEA?"*
> *"Do partnership-acquired users churn faster than organic?"*

Studio Mind answers with a brief like:

```text
FINDING  Nightfall Division's audience collapses at episode 4 (-41% viewers vs ep3).
EVIDENCE [Q3] SELECT ep_number, uniqExact(user_id) ... → rows: (3, 1.2M) (4, 713K) (5, 651K)
CAUSE    Episode 4-5 have the platform's lowest median completion for a flagship
         original [Q5]; churn among ep-4 leavers runs 2.3x the title baseline [Q7].
ACTION   Order a re-edit of episodes 4-5 ahead of the international launch.
```

Every `[Qn]` is clickable in the console: it opens the exact query, its plan, and the
full result table. **No number exists in a brief that was not returned by ClickHouse.**
That is the whole point: an agent with zero hallucination surface.

---

## Live demo

The service runs on **Google Cloud Run** (Dockerfile in repo root, manifest in
[`deploy/service.yaml`](deploy/service.yaml)): unauthenticated HTTP in, one
container kept warm, and the full compliant runtime path on every question —
the HTTP service (`studio_mind/server.py`) drives the pipeline, every warehouse
query goes through the **official `mcp-clickhouse` server**, and every model
call goes to **Gemini 2.5 Flash via Vertex AI** with Application Default
Credentials (runtime service account — no API keys in the container).

```bash
# health (liveness + config; add ?deep=1 to SELECT 1 through mcp-clickhouse)
curl "$URL/health?deep=1"
#   {"status":"ok","transport":"mcp","provider":"vertex","model":"gemini-2.5-flash",
#    "clickhouse":{"ok":true,"version":"26.2.1.558"}}

# ask a real question end-to-end
curl -s -X POST "$URL/ask" -H 'Content-Type: application/json' \
     -d '{"question":"Which genres keep viewers past episode 3 in EMEA?"}'
#   -> brief (markdown), evidence[] (exact SQL + rows per [Qn]), trace_tree,
#      timings_ms per stage, intent. ~40s cold / seconds warm.

# or just open $URL in a browser: type a question, watch the brief, the
# evidence table, and the stage trace render.
```

A captured transcript of this exact flow (local server, container-identical
runtime path, verified 2026-08-26) is committed at
[docs/live-transcript-2026-08-26.md](docs/live-transcript-2026-08-26.md).

**Reproduce the deployment** (no gcloud CLI needed — pure REST with a
service-account key):

```bash
cp .env.example .env          # fill CLICKHOUSE_PASSWORD (trial creds)
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/deployer-key.json
python deploy/deploy.py --region europe-west6
#   Artifact Registry repo -> GCS source upload -> Cloud Build -> Cloud Run v2
#   -> allUsers run.invoker (unauthenticated judges) -> health check + URL
```

Run the same service locally: `python -m studio_mind.server` →
`http://localhost:8080`.

---

## Why this is a ClickHouse project, not a BI dashboard

- **It speaks ClickHouse idioms natively.** Questions compile to cohort retention grids,
  episode funnels, segment cuts, and churn analyses written as real ClickHouse SQL —
  `uniqExact`, window functions, `retention()`, `sumIf`, materialized-view aggregates —
  not `SELECT *` round-trips.
- **It works against any ClickHouse.** Point `CLICKHOUSE_URL` at a local server, WSL,
  or ClickHouse Cloud. The agent introspects your schema (`system.tables`,
  `system.columns`, `DESCRIBE`) and adapts — Studio Mind is an extension of the
  ClickHouse toolchain, not an app welded to one database.
- **The heavy lifting happens inside ClickHouse.** 50M+ events are aggregated in-engine
  through MergeTree partitioning, `LowCardinality` types, and incremental materialized
  views; the LLM never sees raw rows, only query-shaped evidence.

## How it works

A deterministic five-stage pipeline. Gemini (via the official `google-genai` SDK)
powers each stage, but the pipeline — not the model — controls execution:

```text
     ┌─────────┐   ┌─────────┐   ┌──────────┐   ┌───────────┐   ┌────────┐
     │  PARSE  │ → │  QUERY  │ → │ DIAGNOSE │ → │ RECOMMEND │ → │ BRIEF  │
     └─────────┘   └─────────┘   └──────────┘   └───────────┘   └────────┘
     exec question  SQL compiled   hypotheses    ranked actions   one-page
     → analytics    + validated    tested with   w/ expected      brief, every
     intent (JSON)  + executed     MORE queries  impact           number cited
```

1. **PARSE** — the question becomes a structured analytics intent (metrics, dimensions,
   time window, segments) using Gemini structured output.
2. **QUERY** — intent compiles to ClickHouse SQL. Every query is validated
   (read-only AST check, `EXPLAIN`), executed via `clickhouse-connect`, and sanity-checked
   (row counts, magnitudes, empty results) before it is allowed to become evidence.
3. **DIAGNOSE** — Gemini forms hypotheses about *why* the numbers look that way and
   tests each one with additional queries against episode metadata, ad-load exposure,
   release cadence, and cohort behavior. Hypotheses that don't survive the data are
   reported as rejected — not quietly dropped.
4. **RECOMMEND** — ranked programming actions with expected impact, each grounded in
   cited query results.
5. **BRIEF** — a one-page executive brief. Every claim carries an evidence ID that
   resolves to the exact SQL, its execution plan, and the result table.

## The dataset

`data/generate.py` produces **50M+ realistic viewing events** for a fictional streaming
service: 1.2M users, ~180 titles, ~4,500 episodes, six months of timestamps with
believable structure baked in — binge behavior, weekly drop cycles, regional genre
affinity, ad-load friction on the ad-supported tier, weak-episode churn cliffs, and one
flagship original (`Nightfall Division`) whose audience collapses mid-season.

Reproducible: fixed RNG seed, no network needed. Regenerate the whole warehouse:

```bash
python -m data.generate --rows 50000000
```

## Quickstart

```bash
git clone https://github.com/ubongn/clickhouse-studio-mind
cd clickhouse-studio-mind
cp .env.example .env            # add your GEMINI_API_KEY + ClickHouse URL

pip install -e ".[dev]"
python -m data.generate         # build the 50M-event warehouse (defaults in .env)

studio-mind ask "Why did Nightfall Division lose half its audience by episode 5?"
studio-mind ask --show-evidence Q3   # inspect any cited number
```

See `docs/local-clickhouse.md` for running a local ClickHouse (Windows/WSL/macOS/Linux)
and pointing Studio Mind at ClickHouse Cloud instead.

## Status

- [x] Deterministic evidence pipeline (PARSE → QUERY → DIAGNOSE → RECOMMEND → BRIEF)
- [x] 50M-event reproducible dataset + ClickHouse schema (MergeTree, MVs, LowCardinality)
- [x] CLI with evidence inspection
- [ ] MCP server (ask Studio Mind from any MCP client)
- [ ] Web console with clickable evidence (light theme)

## License

MIT — see [LICENSE](LICENSE).
