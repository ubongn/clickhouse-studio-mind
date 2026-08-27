"""Vercel serverless entry — the FastAPI app as a Python function.

Vercel's Python runtime detects the exported ASGI ``app`` and routes every
request through it, so the full HTTP surface (``/``, ``/health``, ``/ask``,
``/examples``) is served by one function defined in ``vercel.json``.

Serverless adaptations live here, before any studio_mind import:

* ``BRIEFS_DIR`` defaults to ``/tmp/briefs`` — the function filesystem is
  read-only except /tmp, and the pipeline writes its brief + trace JSON next
  to each other there (transient; the HTTP response carries everything).
* ``studio_mind`` is imported from the repo root (the function bundles the
  project; the path insert makes that explicit and robust).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if os.getenv("VERCEL"):
    os.environ.setdefault("BRIEFS_DIR", "/tmp/briefs")

from studio_mind.server import app  # noqa: E402 — path bootstrap above
