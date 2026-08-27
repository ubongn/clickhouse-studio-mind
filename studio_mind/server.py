"""HTTP service — the hosted-URL surface for Cloud Run (and local demo).

Endpoints (see README "Live demo"):

  GET  /            judge-facing page: ask a question, see the brief,
                    its evidence (Qn-cited SQL + rows) and the stage trace
  GET  /health      liveness + config report (?deep=1 also SELECT 1 through
                    the official mcp-clickhouse transport)
  POST /ask         {"question": "..."} -> full JSON result (brief, evidence,
                    trace, timings)
  GET  /ask?q=...   same, curl-friendly
  GET  /examples    the three demo questions

Runtime path is the compliant one end-to-end: every warehouse query goes
through the official mcp-clickhouse server (STUDIO_MIND_TRANSPORT=mcp) and
every model call goes to Gemini via Vertex AI (PROVIDER=vertex) using
Application Default Credentials — on Cloud Run that is the service's runtime
service account, no keys in the container.

Run locally:  python -m studio_mind.server   (then open http://localhost:8080)
Container:    uvicorn is started by __main__ on $PORT (Cloud Run default 8080).
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .config import get_settings
from .pipeline.run import run_pipeline

log = logging.getLogger("studio_mind.server")

# --- domain glue -----------------------------------------------------------


class AskBody(BaseModel):
    question: str
    use_llm: bool = True


def _result_payload(result) -> dict:
    """RunResult -> wire JSON (evidence registry parsed for the trust panel)."""
    try:
        evidence = json.loads(result.registry_json)
    except Exception:
        evidence = []
    try:
        trace = json.loads(result.trace_json)
    except Exception:
        trace = {}
    s = get_settings()
    return {
        "question": result.question,
        "intent": result.intent,
        "brief": result.brief,
        "primary_ids": result.primary_ids,
        "timings": result.timings,
        "llm_used": result.llm_used,
        "model": s.llm.model,
        "provider": s.llm.provider,
        "transport": s.ch.transport,
        "evidence": evidence,
        "trace_tree": result.trace_tree,
        "trace": trace,
    }


# --- app -------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="ClickHouse Studio Mind", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health(deep: str | None = Query(default=None)):
        s = get_settings()
        out = {
            "status": "ok",
            "service": "studio-mind",
            "transport": s.ch.transport,
            "database": s.ch.database,
            "provider": s.llm.provider,
            "model": s.llm.model,
        }
        if deep is not None:
            from . import ch

            try:
                client = ch.get_client(s)
                try:
                    row = client.query("SELECT version()").result_rows
                    out["clickhouse"] = {"ok": True, "version": row[0][0] if row else ""}
                finally:
                    ch.close_client(client)
            except Exception as e:  # noqa: BLE001 — health must never raise
                out["status"] = "degraded"
                out["clickhouse"] = {"ok": False, "error": str(e)[:300]}
        return out

    @app.get("/examples")
    def examples():
        from .cli import EXAMPLES

        return {"examples": EXAMPLES}

    @app.post("/ask")
    def ask(body: AskBody):
        q = body.question.strip()
        if not q:
            raise HTTPException(status_code=400, detail="question must not be empty")
        try:
            result = run_pipeline(q, use_llm=body.use_llm)
        except Exception as e:  # surface pipeline errors as 502
            log.exception("pipeline failed")
            raise HTTPException(status_code=502, detail=f"pipeline error: {e}") from e
        return JSONResponse(_result_payload(result))

    @app.get("/ask", response_class=JSONResponse)
    def ask_get(q: str = Query(..., min_length=1), use_llm: bool = True):
        return ask(AskBody(question=q, use_llm=use_llm))

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _INDEX_HTML

    return app


app = create_app()

# --- entrypoint -------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


# --- the page (light theme, inline SVG only, no emoji — house style) ---------

_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ClickHouse Studio Mind</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2024%2024'%3E%3Crect%20width='24'%20height='24'%20rx='5.5'%20fill='%23f5e14b'/%3E%3Cpath%20fill='%231c2430'%20d='M4%204h7v7H4zM13%204h7v4h-7zM13%2010h7v10h-7zM4%2013h7v7H4z'/%3E%3C/svg%3E">
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         background:#f7f8fa; color:#1c2430; }
  header { background:#fff; border-bottom:1px solid #e4e8ee; padding:20px 28px;
           display:flex; align-items:center; gap:14px; }
  header .mark { width:34px; height:34px; border-radius:8px; background:#f5e14b;
                 display:flex; align-items:center; justify-content:center; }
  header h1 { font-size:19px; margin:0; letter-spacing:.2px; }
  header .sub { color:#5b6675; font-size:13px; margin-top:2px; }
  main { max-width:980px; margin:0 auto; padding:28px 24px 80px; }
  .askbar { display:flex; gap:10px; }
  .askbar input { flex:1; padding:13px 16px; font-size:15px; border:1px solid #cfd6e0;
                  border-radius:10px; background:#fff; }
  .askbar input:focus { outline:2px solid #ffd83d; border-color:#f5e14b; }
  .askbar button { padding:13px 22px; font-size:15px; font-weight:600; border:0;
                   border-radius:10px; background:#1c2430; color:#fff; cursor:pointer;
                   display:flex; align-items:center; gap:8px; }
  .askbar button:disabled { opacity:.5; cursor:default; }
  #btn .spin { display:none; width:15px; height:15px; border-radius:50%;
               border:2px solid rgba(255,255,255,.3); border-top-color:#f5e14b;
               animation:btnspin .8s linear infinite; }
  #btn.busy .spin { display:inline-block; }
  #btn.busy svg { display:none; }
  @keyframes btnspin { to { transform:rotate(360deg); } }
  .chips { margin-top:12px; display:flex; flex-wrap:wrap; gap:8px; }
  .chip { font-size:13px; color:#37455a; background:#fff; border:1px solid #dbe1ea;
          padding:6px 12px; border-radius:999px; cursor:pointer; }
  .chip:hover { border-color:#f5e14b; }
  .statusline { margin-top:14px; font-size:13px; color:#5b6675; min-height:18px; }
  .statusline.busy { display:flex; flex-wrap:wrap; align-items:center; gap:6px 12px;
                     min-height:22px; }
  .statusline .pulse { width:9px; height:9px; border-radius:50%; background:#f5e14b;
                       animation:pulse 1.6s ease-out infinite; flex:0 0 auto; }
  @keyframes pulse { 70% { box-shadow:0 0 0 9px rgba(245,225,75,0); }
                     100% { box-shadow:0 0 0 0 rgba(245,225,75,0); } }
  .statusline .st-timer { font-weight:600; color:#1c2430; font-size:14px;
                          font-variant-numeric:tabular-nums; }
  .statusline .st-cold { flex-basis:100%; color:#8a5a00; font-size:12.5px; }
  .sk { border-radius:6px;
        background:linear-gradient(90deg,#eef1f6 25%,#e6ebf2 37%,#eef1f6 63%);
        background-size:400% 100%; animation:shimmer 1.3s ease infinite; }
  @keyframes shimmer { 0% { background-position:100% 0; } 100% { background-position:-100% 0; } }
  .sk-h { height:12px; width:150px; margin-bottom:20px; }
  .sk-pill { height:22px; width:92px; border-radius:999px; display:inline-block;
             margin:0 8px 18px 0; }
  .sk-line { height:13px; margin:9px 0; }
  .w100 { width:100%; } .w95 { width:95%; } .w88 { width:88%; }
  .w70 { width:70%; } .w52 { width:52%; }
  .pill-hot { background:#fbf3c6; color:#6b5c00; }
  .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px;
             overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; border:0; }
  @media (prefers-reduced-motion: reduce) {
    .spin, .statusline .pulse, .sk { animation:none; }
  }
  .card { background:#fff; border:1px solid #e4e8ee; border-radius:12px; padding:22px 24px;
          margin-top:20px; }
  .card h2 { font-size:14px; text-transform:uppercase; letter-spacing:.8px;
             color:#5b6675; margin:0 0 14px; display:flex; align-items:center; gap:8px; }
  .brief { white-space:pre-wrap; font-size:15px; line-height:1.65; }
  .meta { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px; }
  .pill { font-size:12px; padding:4px 10px; border-radius:999px; background:#f1f4f8;
          color:#37455a; }
  .pill b { font-weight:600; }
  table { border-collapse:collapse; width:100%; font-size:13px; }
  th, td { text-align:left; padding:7px 10px; border-bottom:1px solid #edf0f4; }
  th { color:#5b6675; font-weight:600; }
  code, .sql { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; }
  .sql { display:block; background:#f7f8fa; border:1px solid #e4e8ee; border-radius:8px;
         padding:10px 12px; font-size:12.5px; margin:8px 0 10px; white-space:pre-wrap;
         word-break:break-word; }
  .err { color:#b3261e; }
  details { margin-top:10px; }
  summary { cursor:pointer; font-size:13px; color:#37455a; }
  pre.tree { font-size:12px; line-height:1.55; background:#f7f8fa; border:1px solid #e4e8ee;
             border-radius:8px; padding:12px; overflow-x:auto; }
  .ev { margin-bottom:22px; }
  .ev .id { font-weight:700; margin-right:8px; }
  footer { text-align:center; color:#8a93a3; font-size:12px; padding:24px; }
  a { color:#1a5fb4; }
</style>
</head>
<body>
<header>
  <div class="mark">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z" stroke="#1c2430" stroke-width="1.6" stroke-linejoin="round"/>
    </svg>
  </div>
  <div>
    <h1>ClickHouse Studio Mind</h1>
    <div class="sub">Evidence-cited decision briefs over ClickHouse — official mcp-clickhouse runtime, Gemini via Vertex AI</div>
  </div>
</header>
<main id="main">
  <form class="askbar" id="f" onsubmit="return go(event)">
    <input id="q" placeholder="Ask a studio-exec question, e.g. Which genres keep viewers past episode 3 in EMEA?" autofocus>
    <button id="btn" type="submit">
      <svg id="btn-ico" width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="11" cy="11" r="6.5" stroke="#fff" stroke-width="2"/>
        <path d="M16 16l5 5" stroke="#fff" stroke-width="2" stroke-linecap="round"/>
      </svg>
      <span class="spin" aria-hidden="true"></span>
      <span id="btn-label">Ask</span>
    </button>
  </form>
  <div class="chips" id="chips"></div>
  <div class="statusline" id="status"></div>
  <div id="out"></div>
  <div id="sr" class="sr-only" role="status" aria-live="polite"></div>
</main>
<footer>Read-only by construction (sqlguard + server-side readonly). Every number cites its query.</footer>
<script>
const out = document.getElementById('out'), status = document.getElementById('status');
const chips = document.getElementById('chips'), btn = document.getElementById('btn');
const mainEl = document.getElementById('main'), sr = document.getElementById('sr');

// Waiting-UX knobs — /ask takes 45-90s cold, so the page must narrate progress:
// a ticking timer (liveness), honest pipeline stages (the value story), a
// skeleton answer card (no layout jump), a cold-start expectation at 60s, and
// a client-side give-up guard so a wedged request never hangs the judge.
const STAGES = [
  [0,  'Parsing your question…'],
  [3,  'Querying ClickHouse via official MCP server…'],
  [10, 'Diagnosing audience patterns…'],
  [25, 'Writing your brief with SQL receipts…']
];
const COLD_AT = 60;                    // seconds in, show the cold-start note
const COLD_NOTE = 'First question after idle can take ~2 min (ClickHouse Cloud cold resume). Hang tight.';
const FETCH_TIMEOUT_MS = 240000;       // ~240s client-side timeout guard
let tick = null;

fetch('/examples').then(r => r.json()).then(d => {
  d.examples.forEach(q => {
    const c = document.createElement('button');
    c.className = 'chip'; c.textContent = q;
    c.onclick = () => { document.getElementById('q').value = q; };
    chips.appendChild(c);
  });
}).catch(() => {});

function esc(s){ return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function stageFor(s){
  let label = STAGES[0][1];
  for(const [t, l] of STAGES) if(s >= t) label = l;
  return label;
}

function skeleton(){
  return '<div class="card" aria-hidden="true">' +
    '<div class="sk sk-h"></div>' +
    '<span class="sk sk-pill"></span>'.repeat(3) +
    '<div class="sk sk-line w100"></div><div class="sk sk-line w95"></div>' +
    '<div class="sk sk-line w88"></div><div class="sk sk-line w95"></div>' +
    '<div class="sk sk-line w70"></div><div class="sk sk-line w52"></div></div>';
}

function setBusy(on){
  btn.disabled = on;
  btn.classList.toggle('busy', on);
  document.getElementById('btn-label').textContent = on ? 'Thinking…' : 'Ask';
  mainEl.setAttribute('aria-busy', on ? 'true' : 'false');
  if(on){
    status.className = 'statusline busy';
    status.innerHTML = '<span class="pulse" aria-hidden="true"></span>' +
      '<span class="st-timer" aria-hidden="true">Thinking… <span id="st-sec">0</span>s</span>' +
      '<span class="st-stage" id="st-stage">' + STAGES[0][1] + '</span>' +
      '<span class="st-cold" id="st-cold" hidden>' + esc(COLD_NOTE) + '</span>';
    out.innerHTML = skeleton();
  }
}

function startTimer(t0){
  stopTimer();
  let shown = '';
  const step = () => {
    const s = Math.floor((performance.now() - t0) / 1000);
    const sec = document.getElementById('st-sec');
    if(sec) sec.textContent = s;
    const stage = stageFor(s);
    if(stage !== shown){
      shown = stage;
      const el = document.getElementById('st-stage');
      if(el) el.textContent = stage;
      sr.textContent = stage;           // announce stage changes only, not every tick
    }
    if(s >= COLD_AT){
      const c = document.getElementById('st-cold');
      if(c) c.hidden = false;
    }
  };
  step();
  tick = setInterval(step, 1000);
}

function stopTimer(){ if(tick){ clearInterval(tick); tick = null; } }

async function go(ev){
  ev.preventDefault();
  const q = document.getElementById('q').value.trim();
  if(!q || btn.disabled) return false;
  setBusy(true);
  const t0 = performance.now();
  startTimer(t0);
  const ctl = new AbortController();
  const guard = setTimeout(() => ctl.abort(), FETCH_TIMEOUT_MS);
  try {
    const r = await fetch('/ask', {method:'POST', headers:{'Content-Type':'application/json'},
                                   body: JSON.stringify({question: q}), signal: ctl.signal});
    clearTimeout(guard);
    const d = await r.json();
    if(!r.ok) throw new Error(d.detail || r.statusText);
    const secs = Math.max(1, Math.round((performance.now() - t0) / 1000));
    status.className = 'statusline';
    status.textContent = 'Answered in ' + secs + 's.';
    sr.textContent = 'Answered in ' + secs + 's.';
    render(d, secs);
  } catch(e) {
    clearTimeout(guard);
    const msg = e.name === 'AbortError'
      ? 'Gave up after ' + Math.round(FETCH_TIMEOUT_MS / 1000) + 's — the pipeline seems stuck (usually a cold warehouse or a wedged model call). Please Ask again.'
      : 'Request failed: ' + e.message;
    status.className = 'statusline'; status.textContent = '';
    sr.textContent = 'The request failed. ' + msg;
    out.innerHTML = '<div class="card"><h2>Something went wrong</h2>' +
      '<div class="err">' + esc(msg) + '</div>' +
      '<div style="margin-top:10px;font-size:13px;color:#5b6675">Your question is still in the box above — press Ask to retry.</div></div>';
  } finally {
    stopTimer();
    setBusy(false);
  }
  return false;
}

function render(d, secs){
  const ms = Object.entries(d.timings || {}).map(([k,v]) =>
      '<span class="pill"><b>' + esc(k.replace('_ms','')) + '</b> ' + Number(v).toFixed(0) + ' ms</span>').join('');
  let ev = (d.evidence || []).map(e =>
    '<div class="ev"><div><span class="id">[' + esc(e.id) + ']</span> ' + esc(e.purpose) + '</div>' +
    '<code class="sql">' + esc(e.sql) + '</code>' +
    (e.error ? '<div class="err">' + esc(e.error) + '</div>' :
      '<table><tr>' + (e.columns||[]).map(c=>'<th>'+esc(c)+'</th>').join('') + '</tr>' +
      (e.rows||[]).slice(0,12).map(r=>'<tr>'+r.map(v=>'<td>'+esc(v===null?'NULL':v)+'</td>').join('')+'</tr>').join('') +
      '</table>')
    + '</div>').join('');
  out.innerHTML =
    '<div class="card"><h2><svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M6 3h12v18l-6-4-6 4z" stroke="#5b6675" stroke-width="1.8" stroke-linejoin="round"/></svg>Decision brief</h2>' +
    '<div class="meta">' + (secs ? '<span class="pill pill-hot"><b>answered</b> in ' + secs + 's</span>' : '') + ms + '<span class="pill"><b>model</b> ' + esc(d.model) + ' · ' + esc(d.provider) + '</span>' +
    '<span class="pill"><b>transport</b> ' + esc(d.transport) + '</span></div>' +
    '<div class="brief">' + esc(d.brief) + '</div></div>' +
    (ev ? '<div class="card"><h2><svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M9 5h9M9 12h9M9 19h9M4 5h.01M4 12h.01M4 19h.01" stroke="#5b6675" stroke-width="2" stroke-linecap="round"/></svg>Evidence — every number cites its query</h2>' + ev + '</div>' : '') +
    (d.trace_tree ? '<div class="card"><details><summary>Stage trace (spans)</summary><pre class="tree">' + esc(d.trace_tree) + '</pre></details></div>' : '');
}
</script>
</body>
</html>
"""
