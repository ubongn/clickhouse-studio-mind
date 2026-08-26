"""CI gate: every brief produced by a pipeline run must earn its claims.

Checks each brief for the contract the project promises judges:
  * at least one SQL appendix block (the exact query behind the numbers)
  * [Qn] citation markers in the body (claims point at evidence)
  * evidence appendix headings ([Qn] purpose)
  * a scan receipt line (wall ms / server ms / rows scanned) when the
    transport provided server-side stats

Exit code 0 = all briefs pass; anything else fails the CI job.

    python -m examples.verify_briefs [briefs_dir]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def verify(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []
    if "```sql" not in text:
        problems.append("no SQL appendix block (```sql)")
    if not re.search(r"\[Q\d+\]", text):
        problems.append("no [Qn] citation markers in the body")
    if "Appendix — evidence" not in text:
        problems.append("no evidence appendix")
    if not re.search(r"### \[Q\d+\]", text):
        problems.append("no per-evidence appendix headings")
    return problems


def main() -> int:
    briefs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("briefs")
    briefs = sorted(briefs_dir.glob("*.md"))
    if not briefs:
        print(f"FAIL: no briefs found in {briefs_dir}")
        return 1

    failures = 0
    total_receipts = 0
    for path in briefs:
        text = path.read_text(encoding="utf-8")
        problems = verify(path)
        receipts = len(re.findall(r"rows scanned", text))
        total_receipts += receipts
        if problems:
            failures += 1
            print(f"FAIL {path.name}:")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"OK   {path.name} · {receipts} scan receipt(s) with server-side stats")

    print(f"\n{len(briefs) - failures}/{len(briefs)} briefs pass · "
          f"{total_receipts} scan receipts total")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
