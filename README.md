# ClickHouse Studio Mind

**Hosted URL (live demo):** <!-- HOSTED_URL --> `https://studio-mind-ubongns-projects.vercel.app` — one-question demo, no login; verification transcript: [docs/live-transcript-2026-08-26.md](docs/live-transcript-2026-08-26.md) · serverless on Vercel (Hobby plan, fluid compute); runtime: official mcp-clickhouse → ClickHouse Cloud, Gemini via Vertex AI. (Google Cloud Run deploy files kept in [`deploy/`](deploy/) as an alternative host.)

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

The service is **deployed serverless on Vercel** (Hobby plan, fluid compute,
Python 3.12 — packaging: [`api/index.py`](api/index.py) exposes the FastAPI app
as a Vercel function, [`vercel.json`](vercel.json) sets the routing and a 300s
`maxDuration`). Every question runs the full compliant runtime path — the HTTP
service (`studio_mind/server.py`) drives the pipeline, every warehouse query
goes through the **official `mcp-clickhouse` server**, and every model call
goes to **Gemini 2.5 Flash via Vertex AI**. No API keys at runtime: on Vercel
the Vertex service account is injected as the `GOOGLE_CREDENTIALS_JSON` env
string (adapter: `studio_mind/config.py — vertex_credentials()`), with key-file
and ADC fallbacks for local/Cloud Run. The same container is also packaged for
**Google Cloud Run** (Dockerfile in repo root, manifest in
[`deploy/service.yaml`](deploy/service.yaml), one-command deploy) — kept as an
alternative host.

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

A captured transcript of this exact flow (container-identical runtime path,
verified 2026-08-26) is committed at
[docs/live-transcript-2026-08-26.md](docs/live-transcript-2026-08-26.md).

**Alternative host — Google Cloud Run** (same image, same runtime path):
reproduce the deployment with pure REST, no gcloud CLI:

```bash
cp .env.example .env          # fill CLICKHOUSE_PASSWORD (trial creds)
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/deployer-key.json
python deploy/deploy.py --region europe-west6
#   Artifact Registry repo -> GCS source upload -> Cloud Build -> Cloud Run v2
#   -> allUsers run.invoker (unauthenticated judges) -> health check + URL
```

Run the same service locally: `python -m studio_mind.server` →
`http://localhost:8080`.

The stack, end to end:

```text
 browser / curl ──HTTPS──▶ Vercel function (FastAPI ASGI, api/index.py)
                                        (studio_mind/server.py)
                                        │  deterministic five-stage pipeline (Python)
                                        ├──▶ official mcp-clickhouse server   [stdio subprocess]
                                        │         └──▶ ClickHouse Cloud       [HTTP, read-only]
                                        └──▶ Gemini 2.5 Flash via Vertex AI   [service-account creds]
```

---

## Runtime evidence (partner API in the runtime path)

Every runtime analytics query goes through the **official ClickHouse MCP server**
([github.com/ClickHouse/mcp-clickhouse](https://github.com/ClickHouse/mcp-clickhouse))
— in the served demo and by default, not just in CI or docs. Exact anchors:

| What | Where |
|---|---|
| Official server module pinned | `studio_mind/mcp_transport.py:50` — `MCP_SERVER_MODULE = "mcp_clickhouse.main"` |
| Spawned as a stdio subprocess | `studio_mind/mcp_transport.py:201` (command) and `:218` (`StdioServerParameters`) |
| Official tool surface discovered | `studio_mind/mcp_transport.py:229` — `session.list_tools()` |
| Official `run_query` tool invoked | `studio_mind/mcp_transport.py:231` (selection) and `:247` — `session.call_tool(...)` |
| Default transport is `mcp` | `studio_mind/config.py:105` (dataclass default) and `:171` (`STUDIO_MIND_TRANSPORT` env default) |
| Transport wiring | `studio_mind/ch.py:55` — `transport=mcp → McpClient` (http → clickhouse-connect dev fallback) |
| HTTP service runs it | `studio_mind/server.py:97` (`/health?deep=1` → `SELECT version()` via MCP) and `:118` (`/ask` → full pipeline) |
| Cloud Run sets it | `deploy/service.yaml:57` — `STUDIO_MIND_TRANSPORT: mcp` |
| Vercel runs it too | `api/index.py` — same FastAPI app as a serverless function |
| Official package is a runtime dep | `pyproject.toml:11` — `mcp-clickhouse>=0.4.1` (PyPI) |

Verify yourself: `grep -rn "mcp_clickhouse" studio_mind/` — every hit is the runtime path.

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
   (read-only AST check, `EXPLAIN`), executed through the **official `mcp-clickhouse`
   server** (see [Runtime evidence](#runtime-evidence-partner-api-in-the-runtime-path);
   the direct `clickhouse-connect` client is the bulk-seed / dev-fallback path), and
   sanity-checked (row counts, magnitudes, empty results) before it is allowed to
   become evidence.
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

**Cloud** — three options: use the hosted demo (URL at the top of this README),
deploy your own on Vercel (`api/index.py` + `vercel.json` are committed;
`vercel --prod` after setting the `CLICKHOUSE_*` / `PROVIDER=vertex` /
`GOOGLE_CREDENTIALS_JSON` env vars), or deploy to Google Cloud Run with the
one-command REST deploy:

```bash
cp .env.example .env                              # fill CLICKHOUSE_PASSWORD
export GOOGLE_APPLICATION_CREDENTIALS=key.json    # deployer service-account key
python deploy/deploy.py --region europe-west6     # build + deploy + public URL
```

## Status

- [x] Deterministic evidence pipeline (PARSE → QUERY → DIAGNOSE → RECOMMEND → BRIEF)
- [x] 50M-event reproducible dataset + ClickHouse schema (MergeTree, MVs, LowCardinality)
- [x] CLI with evidence inspection
- [x] Web console with clickable evidence, light theme — `python -m studio_mind.server`
- [x] One-command Cloud Run deploy (pure REST, no gcloud CLI) — `deploy/deploy.py`
- [x] Serverless Vercel deploy (Hobby plan) — `api/index.py` + `vercel.json`
- [x] Live hosted URL — `https://studio-mind-ubongns-projects.vercel.app`
- [ ] MCP server (ask Studio Mind from any MCP client)

## License

MIT — see [LICENSE](LICENSE).
