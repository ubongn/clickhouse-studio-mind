"""LLM provider layer — Google Gemini today, Vertex AI tomorrow.

The Google Cloud runtime requirement is satisfied by this module: the agent's
model calls go through the official `google-genai` SDK, and the provider is
selected by environment (PROVIDER=gemini|vertex):

  * PROVIDER=gemini  — development: GEMINI_API_KEY (AI Studio key)
  * PROVIDER=vertex  — production: GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION
                       (Application Default Credentials; service account)

TODO(GCP): once the Google Cloud project is provisioned (~Aug 30), flip
PROVIDER=vertex and set the project/location env vars. No other code changes —
every stage calls LLM.generate()/LLM.structured() through this layer only.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .config import Settings, get_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLM:
    """Thin, swappable wrapper over google-genai. One method surface:

    generate(prompt, system)  -> str
    structured(prompt, schema, system) -> dict   (JSON schema enforced)
    """

    def __init__(self, settings: "Settings | LlmSettings | None" = None):
        from .config import LlmSettings, get_settings
        if settings is None:
            self.s = get_settings().llm
        elif isinstance(settings, LlmSettings):
            self.s = settings
        else:
            self.s = settings.llm  # full Settings passed
        self._client = None
        self.model = self.s.model

    # -- client -------------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            from google import genai  # official Google GenAI SDK

            if self.s.is_vertex:
                if not (self.s.google_cloud_project and self.s.google_cloud_location):
                    raise LLMError(
                        "PROVIDER=vertex requires GOOGLE_CLOUD_PROJECT and "
                        "GOOGLE_CLOUD_LOCATION (Google Cloud runtime)"
                    )
                # Google Cloud runtime path: Vertex AI + ADC
                self._client = genai.Client(
                    vertexai=True,
                    project=self.s.google_cloud_project,
                    location=self.s.google_cloud_location,
                )
            else:
                if not self.s.api_key:
                    raise LLMError("GEMINI_API_KEY missing (or set PROVIDER=vertex)")
                self._client = genai.Client(api_key=self.s.api_key)
        return self._client

    # -- API ------------------------------------------------------------------
    def generate(self, prompt: str, system: str = "") -> str:
        from . import tracing

        with tracing.maybe_llm_span("gemini · generate", self.model, prompt) as span:
            try:
                resp = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config={"system_instruction": system} if system else None,
                )
                text = (resp.text or "").strip()
                if not text:
                    raise LLMError("empty model response")
                span.output = text[:500]
                span.usage = _usage(resp)
                return text
            except LLMError:
                raise
            except Exception as e:  # SDK errors → single error type for callers
                raise LLMError(f"gemini call failed: {e}") from e

    def structured(self, prompt: str, schema: dict, system: str = "") -> dict:
        """Schema-enforced JSON response. `schema` is a JSON-schema dict."""
        from . import tracing

        with tracing.maybe_llm_span("gemini · structured", self.model, prompt) as span:
            try:
                resp = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config={
                        **({"system_instruction": system} if system else {}),
                        "response_mime_type": "application/json",
                        "response_schema": schema,
                    },
                )
                text = (resp.text or "").strip()
                if not text:
                    raise LLMError("empty structured response")
                span.output = text[:500]
                span.usage = _usage(resp)
                return json.loads(text)
            except json.JSONDecodeError as e:
                raise LLMError(f"model returned invalid JSON: {e}") from e
            except LLMError:
                raise
            except Exception as e:
                raise LLMError(f"gemini structured call failed: {e}") from e


def _usage(resp) -> dict[str, int]:
    """Token usage from a google-genai response, when the SDK reports it."""
    u = getattr(resp, "usage_metadata", None)
    if u is None:
        return {}
    return {
        "input": getattr(u, "prompt_token_count", 0) or 0,
        "output": getattr(u, "candidates_token_count", 0) or 0,
        "total": getattr(u, "total_token_count", 0) or 0,
    }
