# Demo-day runbook (video due ~Sep 7, 2026)

Target: a screen-recording of the live hosted demo, ≤ 3:00 per hackathon rules.
Everything below assumes the public URL:

> **https://clickhouse-studio-mind.vercel.app** — no login required.

---

## Pre-roll checklist (do these 30-60 min BEFORE recording)

1. **URL health (deep):**
   `curl "https://clickhouse-studio-mind.vercel.app/health?deep=1"`
   Expect: `{"status":"ok","transport":"mcp","provider":"vertex","model":"gemini-2.5-flash","clickhouse":{"ok":true,...}}`.
   If `status` is not `ok` or `clickhouse.ok` is false → see Fallbacks below.
2. **Wake ClickHouse Cloud.** After long idle the service can **cold-resume for up to ~2 min** — the first query just hangs, later ones are fast. Warm it by running the deep health check AND one real `/ask` (the EMEA question) ~30-60 min before recording, then again ~5 min before. Recording should then hit a warm warehouse.
3. **Vertex probe.** The deep health check already proves the Gemini/Vertex path (`provider":"vertex"`). For belt-and-braces, run one real `/ask` and confirm the brief renders with `llm_used=true` behavior (it always is on the live path).
4. **Example buttons.** Open the URL in a fresh browser window: three example-question chips must render under the input box (they load from `/examples`). Verified working 2026-08-27.
5. **Browser/recording setup:** fresh profile or incognito (no stale state), light theme, window zoom ~125-150% so the brief reads well at 1080p, screen recorder at 1080p+, Do Not Disturb ON, close tabs/notifications.
6. **Clipboard:** copy the exact question string (below) so the paste is typo-free.

## The exact EMEA question string (copy verbatim)

```text
Which genres keep viewers past episode 3 in EMEA?
```

It is also the middle example chip on the page — clicking the chip is the
typo-proof way to load it.

## What to show on camera (suggested order)

1. **README, ~15s** — show the top of `github.com/ubongn/clickhouse-studio-mind`: hosted URL block ("no login required"), CI badge, MIT badge, "Judges start here". One sentence: "every number in the brief cites the SQL that produced it."
2. **Judge page, ~10s** — open the hosted URL; point out the three example chips and the read-only footer.
3. **Ask live, ~60s** — click the EMEA chip (or paste the string), press **Ask**. The status line shows the pipeline running. **Narrate while waiting** (see timing budget): parse → compile ClickHouse SQL → execute via the official mcp-clickhouse server → diagnose → recommend → brief.
4. **Brief, ~40s** — read the FINDING out loud; then **click a `[Qn]` evidence citation** to open its SQL receipt: exact query, plan, result rows. Say: "no number exists in the brief that ClickHouse didn't return."
5. **Stage trace, ~15s** — expand the "Stage trace (spans)" card under the brief; show per-stage timings (parse/query/diagnose/recommend/brief) from `tracings.py` spans.
6. **Runtime-evidence anchors, ~15s** — scroll the README to "Runtime evidence": file:line anchors proving the official ClickHouse MCP server and Vertex Gemini are the runtime path, not docs-only claims. Close on the CI badge + MIT license.

Optional (only if the first answer was fast): run the churn question as a second example.

## Timing budget

- **Full answer: ~45-60s end-to-end** (verified 47.5s on 2026-08-27; cold ClickHouse can add up to ~2 min — hence the warm-up steps above).
- The wait is **not dead air — it is the demo**: narrate the five stages while the status line runs. If it finishes while you talk, that's fine; finish the sentence, then walk the brief.
- Video budget inside 3:00: intro/README 20s + live ask 60s + brief & SQL receipt 40s + trace 15s + anchors/close 20s ≈ **2:35**, leaving slack for a slow answer.

## Fallbacks

- **Vercel slow/down:** warm it again (deep health + one `/ask`), wait out a ClickHouse cold-resume (up to ~2 min), retry.
- **URL unusable:** run locally `python -m studio_mind.server` → `http://localhost:8080` (same app, same runtime path) and record that; say "same code as the hosted deployment."
- **Example chips missing** (endpoint hiccup): just type the EMEA question manually — the demo does not depend on the chips.
