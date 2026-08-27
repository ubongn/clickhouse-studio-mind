"""Vercel serverless entry — the FastAPI app as a Python function.

Vercel's Python runtime detects an exported ASGI ``app`` and routes every
request through it, so the full HTTP surface (``/``, ``/health``, ``/ask``,
``/examples``) is served by one function defined in ``vercel.json``.

Serverless adaptations live here, before any studio_mind import:

* ``BRIEFS_DIR`` defaults to ``/tmp/briefs`` — the function filesystem is
  read-only except /tmp, and the pipeline writes its brief + trace JSON next
  to each other there (transient; the HTTP response carries everything).
* ``studio_mind`` is imported from the repo root (the function bundles the
  project; the path insert makes that explicit and robust).
* ``VercelRewritePathASGI`` — the Vercel build pipeline (CLI 59.x /
  UI-import projects) forwards internal rewrites to the app using the
  *rewritten* destination path, so the FastAPI app saw ``/api/index`` for
  every request and 404'd on all real routes. ``vercel.json`` now carries
  the client's original path through the rewrite as the ``__vc_path`` query
  parameter; the middleware below puts it back into the ASGI scope (and
  strips the marker param) before FastAPI routes the request. Without the
  param (old pipeline, uvicorn, Cloud Run) the scope passes through
  untouched. Vercel's Python runtime accepts any ASGI callable, so the
  exported ``app`` is this wrapper around the FastAPI instance
  (still importable, unchanged, from ``studio_mind.server``).
"""

from __future__ import annotations

import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if os.getenv("VERCEL"):
    os.environ.setdefault("BRIEFS_DIR", "/tmp/briefs")

from studio_mind.server import app as _fastapi_app  # noqa: E402 — path bootstrap above

_PATH_PARAM = "__vc_path"


class VercelRewritePathASGI:
    """ASGI middleware restoring the request path a Vercel rewrite swallowed.

    On ``http`` scopes: if the query string carries ``__vc_path`` (set by the
    ``vercel.json`` rewrite), rebuild the scope with that value as ``path``/
    ``raw_path`` and with the marker param removed from ``query_string`` so
    the app sees exactly what the client asked for. Other scopes and requests
    without the param are forwarded untouched.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            path, query_string = self._split_path_param(scope.get("query_string", b""))
            if path is not None:
                scope = dict(scope)  # never mutate the caller's scope dict
                scope["path"] = path or "/"
                scope["raw_path"] = scope["path"].encode("utf-8")
                scope["query_string"] = query_string
        await self.app(scope, receive, send)

    @staticmethod
    def _split_path_param(query_string: bytes) -> tuple[str | None, bytes]:
        """Return ``(path_or_None, remaining_query_string)``.

        ``parse_qsl`` already percent-decodes the value (paths arrive encoded
        by the rewrite). Duplicate markers collapse to the first; every
        marker occurrence is stripped from the remainder so downstream query
        params (``deep``, ``q``, ...) survive verbatim.
        """
        pairs = urllib.parse.parse_qsl(
            query_string.decode("latin-1"), keep_blank_values=True
        )
        path: str | None = None
        rest: list[tuple[str, str]] = []
        for key, value in pairs:
            if key == _PATH_PARAM:
                if path is None:
                    path = value
            else:
                rest.append((key, value))
        remaining = urllib.parse.urlencode(rest).encode("latin-1")
        return path, remaining


app = VercelRewritePathASGI(_fastapi_app)
