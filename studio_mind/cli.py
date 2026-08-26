"""CLI: studio-mind ask "question" [--show-evidence Qn] [--no-llm] [--json]"""

from __future__ import annotations

import argparse
import json
import sys

from .config import get_settings
from .llm import LLM, LLMError
from .pipeline.run import run_pipeline

EXAMPLES = [
    'Why did Nightfall Division lose so much of its audience by episode 5?',
    "Which genres keep viewers past episode 3 in EMEA?",
    "Do partnership-acquired users churn faster than organic users?",
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="studio-mind",
        description="ClickHouse Studio Mind — evidence-cited analytics for studio executives",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask", help="ask an exec question, get a decision brief")
    ask.add_argument("question")
    ask.add_argument("--no-llm", action="store_true",
                     help="deterministic pipeline only (no Gemini calls)")
    ask.add_argument("--json", action="store_true", help="emit machine-readable result")
    ask.add_argument("--quiet", action="store_true", help="print only the brief path")

    show = sub.add_parser("evidence", help="inspect evidence from a saved brief run")
    show.add_argument("run_json", help="path to a .run.json produced by ask --json")

    sub.add_parser("examples", help="print example questions")

    args = ap.parse_args(argv)

    if args.cmd == "examples":
        print("\n".join(f"- {q}" for q in EXAMPLES))
        return 0

    if args.cmd == "evidence":
        data = json.load(open(args.run_json, encoding="utf-8"))
        for ev in json.loads(data["registry"]):
            print(f"\n=== [{ev['id']}] {ev['purpose']}")
            print(ev["sql"])
            if ev.get("error"):
                print("ERROR:", ev["error"])
            else:
                cols = ev.get("columns", [])
                print(" | ".join(map(str, cols)))
                for r in ev.get("rows", [])[:20]:
                    print(" | ".join(map(str, r)))
        return 0

    settings = get_settings()

    # fail fast with a clear message if the LLM is required but unavailable
    if not args.no_llm:
        try:
            LLM(settings).client  # noqa: B018 — property access validates config
        except LLMError as e:
            print(f"[studio-mind] LLM unavailable ({e}); continuing in deterministic mode.\n"
                  f"               Set GEMINI_API_KEY in .env for full capability.",
                  file=sys.stderr)

    result = run_pipeline(args.question, settings, use_llm=not args.no_llm)

    if args.json:
        out = {
            "question": result.question,
            "intent": result.intent,
            "brief": result.brief,
            "brief_path": result.brief_path,
            "primary_ids": result.primary_ids,
            "timings": result.timings,
            "llm_used": result.llm_used,
            "registry": result.registry_json,
        }
        json_path = result.brief_path.replace(".md", ".run.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, default=str)
        if not args.quiet:
            print(result.brief)
            print(f"\n[evidence bundle] {json_path}")
        else:
            print(json_path)
    else:
        print(result.brief)
        print(f"\n[saved] {result.brief_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
