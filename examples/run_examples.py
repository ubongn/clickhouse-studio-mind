"""Milestone gate: three executive questions through the full pipeline.

Each question must produce a brief whose every number is evidence-cited, with
real Gemini calls and real ClickHouse queries. Exit code 0 = all three answered.

    python -m examples.run_examples
    python -m examples.run_examples --no-llm     # deterministic path
"""

from __future__ import annotations

import argparse
import sys

QUESTIONS = [
    "Why did Nightfall Division lose so much of its audience by episode 5?",
    "Which genres keep viewers past episode 3 in EMEA?",
    "Do partnership-acquired users churn faster than organic users?",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    from studio_mind.config import get_settings
    from studio_mind.pipeline.run import run_pipeline

    settings = get_settings()
    failures = 0
    for q in QUESTIONS:
        print("=" * 78)
        print(f"QUESTION: {q}")
        print("=" * 78)
        try:
            result = run_pipeline(q, settings, use_llm=not args.no_llm)
            print(result.brief[:2400])
            print(f"\n→ full brief: {result.brief_path}")
            print(f"→ evidence queries: {len(result.registry_json)} bytes, "
                  f"llm={'yes' if result.llm_used else 'no'}, "
                  f"timings={ {k: round(v) for k, v in result.timings.items()} }")
        except Exception as e:
            failures += 1
            print(f"FAILED: {e}")
    print("=" * 78)
    print("ALL OK" if failures == 0 else f"{failures}/3 FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
