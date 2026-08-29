# Demo-day runbook (video due ~Sep 7, 2026)

Target: a screen-recording of the live hosted demo, ≤ 3:00 per hackathon rules.
The authoritative shot-by-shot script + timing is
[`docs/video/script.md`](video/script.md) — this file is the pre-roll
checklist and fallbacks. Everything assumes the public URL:

> **https://clickhouse-studio-mind.vercel.app** — no login required.

---

## Pre-roll checklist (do these 30-60 min BEFORE recording)

1. **URL health (deep):**
   `curl "https://clickhouse-studio-mind.vercel.app/health?deep=1"`
   Expect: `{"status":"ok","transport":"mcp","provider":"vertex","model":"gemini-2.5-flash","clickhouse":{"ok":true,...}}`.
   If `status` is not `ok` or `clickhouse.ok` is false → see Fallbacks below.
2. **Wake ClickHouse Cloud.** After long idle the service can **cold-resume for up to ~2 min** — the first query just hangs, later ones are fast. Warm it by running the deep health check AND one real `/ask` (the EMEA question) ~30-60 min before recording, then again ~5 min before. Recording should then hit a warm warehouse.
3. **Vertex probe.** The deep health check already proves the Gemini/Vertex path (`provider":"vertex"`). For belt-and-braces, run one real `/ask` and confirm the brief renders with `llm_used=true` behavior (it always is on the live path).
3b. **50M-row reload (if the warehouse still holds the 200k seed).** The
script's trust-panel lines assume `50,000,000 rows scanned` on camera. Check
any evidence receipt's scan pill; if it shows ~200k, reload the full dataset
(~1-2 h ahead of recording): `python -m data.generate --rows 50000000 &&
python -m data.load`, then warm the service again (deep health + one /ask).
3c. **Morning brief dry run.** Open `/morning` once (or the dashed chip on
the page). If the watchlist is empty on the live day, the take pins the
planted CDN-incident morning: navigate to `/morning?date=2026-05-21` on
camera ("let's look at the morning of May 21st") — the watchlist always
lights up there.
4. **Example buttons.** Open the URL in a fresh browser window: three example-question chips must render under the input box (they load from `/examples`). Verified working 2026-08-27.
5. **Browser/recording setup:** fresh profile or incognito (no stale state), light theme, window zoom ~125-150% so the brief reads well at 1080p, screen recorder at 1080p+, Do Not Disturb ON, close tabs/notifications.
6. **Clipboard:** copy the exact question string (below) so the paste is typo-free.

## The exact EMEA question string (copy verbatim)

```text
Which genres keep viewers past episode 3 in EMEA?
```

It is also the middle example chip on the page — clicking the chip is the
typo-proof way to load it.

## What to show on camera (authoritative order: docs/video/script.md)

1. **README, ~12s** — show the top of `github.com/ubongn/clickhouse-studio-mind`: hosted URL block ("no login required"), CI badge, MIT badge, "Judges start here". One sentence: "every number in the brief cites the SQL that produced it."
2. **Judge page, ~8s** — open the hosted URL; point out the three example chips and the dashed morning-brief chip.
3. **Ask live, ~35s** — click the EMEA chip (or paste the string), press **Ask**. The status line shows the pipeline running. **Narrate while waiting** (see timing budget): parse → compile ClickHouse SQL → execute via the official mcp-clickhouse server → diagnose → recommend → brief.
4. **Brief + citation chain, ~35s** — read the FINDING out loud; scroll to show `[Qn]` citations; the **scan pill under every SQL receipt** ("312 ms wall · 245 ms server · 50,000,000 rows scanned · 180 MiB") is the money shot — say: "no number exists in the brief that ClickHouse didn't return."
5. **Stage trace, ~10s** — expand the "Stage trace (spans)" card under the brief; show per-stage timings and the nested MCP tool spans.
6. **Morning brief closer, ~30s** — click the dashed chip: metrics table (yesterday vs 7-day baseline, Δ, z), Watchlist card with level/z pills, QoE attribution naming NA · mobile. Say: "a CDN incident caught before the support tickets pile up."
7. **Close, ~8s** — footer ("Read-only by construction — every number cites its query") + end card with URLs.

## Timing budget

- **Full answer: ~45-60s end-to-end** (verified 47.5s on 2026-08-27; cold ClickHouse can add up to ~2 min — hence the warm-up steps above).
- The wait is **not dead air — it is the demo**: narrate the five stages while the status line runs. If it finishes while you talk, that's fine; finish the sentence, then walk the brief.
- Video budget inside 3:00 (full table in `docs/video/script.md`): cold open 18s + live ask (incl. narrated wait) 34s + answer/citation chain 60s + repo proof 12s + morning brief 38s + close 16s ≈ **2:58**. If the live answer runs long, trim the repo shot — never cut the scan pill, the span tree, or the morning brief.

## Fallbacks

- **Vercel slow/down:** warm it again (deep health + one `/ask`), wait out a ClickHouse cold-resume (up to ~2 min), retry.
- **URL unusable:** run locally `python -m studio_mind.server` → `http://localhost:8080` (same app, same runtime path) and record that; say "same code as the hosted deployment."
- **Example chips missing** (endpoint hiccup): just type the EMEA question manually — the demo does not depend on the chips.
- **Morning watchlist empty:** pin the incident day — `/morning?date=2026-05-21` (the planted CDN-incident week always lights up).
- **50M reload impossible in time:** keep the seed but re-voice shot 7 to the real scan pill values on screen — never say a number that isn't visible.
