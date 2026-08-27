# Waiting-UX evidence (M7-UX)

Before/after captures of the judge page while `/ask` runs (45-90s in
production). Reproduce locally with the offline stub — it serves the real
page with a fake, deliberately slow pipeline:

    STUB_DELAY=95 python scripts/stub_server.py     # http://127.0.0.1:8099

| file | state |
|---|---|
| `before-waiting-3s.png` / `before-waiting-14s.png` | OLD: static gray statusline, half-opacity button — reads as broken |
| `before-complete.png` | OLD completed state (no client-elapsed pill) |
| `after-1s-parsing.png` | spinner + "Thinking… 1s" + "Parsing your question…" + skeleton card |
| `after-7s-querying.png` | stage 2: "Querying ClickHouse via official MCP server…" |
| `after-19s-diagnosing.png` | stage 3: "Diagnosing audience patterns…" |
| `after-40s-writing.png` | stage 4: "Writing your brief with SQL receipts…" |
| `after-46s-writing-mobile.png` | same, 390px mobile viewport |
| `after-74s-coldstart.png` | >60s: cold-start note "…can take ~2 min (ClickHouse Cloud cold resume). Hang tight." |
| `after-answered.png` | done: "Answered in 75s." + highlighted `answered in 75s` meta pill |
| `after-error-card.png` | error path: friendly card + retry hint, button re-enabled |
