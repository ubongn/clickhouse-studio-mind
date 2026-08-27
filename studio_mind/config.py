"""Typed settings for Studio Mind.

Everything is configured through the environment (see ``.env.example``).
Defaults match the documented ClickHouse Cloud / localhost shapes so a
``cp .env.example .env`` plus an API key is enough to run.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional at import time so unit tests can run without python-dotenv
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a hard dep in practice
    load_dotenv = None


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    return int(value)


_VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def vertex_credentials():
    """Google service-account credentials without needing a key file.

    Precedence (adapter for serverless hosts where no secret file can be
    shipped, Vercel first):

    1. ``GOOGLE_CREDENTIALS_JSON``  — the full service-account JSON as one
       string (``json.dumps`` of the key file). Used by the Vercel
       deployment; the value lives only in the platform's env store.
    2. ``GOOGLE_APPLICATION_CREDENTIALS`` — key-file path (local dev and
       Cloud Run; same effect as ADC picking the file up itself).
    3. ``None`` — caller falls back to Application Default Credentials
       (GCE metadata server, ``gcloud auth application-default``).

    Returns an authenticated ``google.oauth2.service_account.Credentials``
    or ``None``. Raises ``ValueError`` on a malformed JSON string so a bad
    env var is loud, not silent.
    """
    raw = (os.getenv("GOOGLE_CREDENTIALS_JSON") or "").strip()
    if raw:
        from google.oauth2 import service_account

        info = json.loads(raw)
        return service_account.Credentials.from_service_account_info(
            info, scopes=[_VERTEX_SCOPE]
        )

    key_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if key_file and Path(key_file).is_file():
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(
            key_file, scopes=[_VERTEX_SCOPE]
        )

    return None


@dataclass(frozen=True)
class LlmSettings:
    """LLM provider settings — Google only, by hackathon rule.

    ``provider`` selects between the Gemini API key path (development) and
    Vertex AI (Google Cloud production path). The provider layer in
    ``studio_mind.llm`` swaps automatically.
    """

    provider: str = "gemini"
    api_key: str = ""
    model: str = "gemini-2.5-flash"
    google_cloud_project: str = ""
    google_cloud_location: str = "us-central1"

    @property
    def is_vertex(self) -> bool:
        return self.provider.strip().lower() == "vertex"


@dataclass(frozen=True)
class ClickHouseSettings:
    """Connection settings for the analytics warehouse."""

    url: str = "http://localhost:8123"
    user: str = "default"
    password: str = ""
    database: str = "studio"
    # "mcp" is the runtime default: every analytics query goes through the
    # official mcp-clickhouse server (github.com/ClickHouse/mcp-clickhouse).
    # "http" is the dev fallback (direct clickhouse-connect, no MCP layer).
    transport: str = "mcp"

    @property
    def is_secure(self) -> bool:
        return self.url.startswith("https://")


@dataclass(frozen=True)
class GeneratorSettings:
    """Knobs for the synthetic warehouse generator (``python -m data.generate``)."""

    rows: int = 50_000_000
    seed: int = 20260826
    batch_size: int = 500_000
    out_dir: Path = field(default_factory=lambda: Path("data") / "warehouse")
    load: bool = True  # stream batches into ClickHouse as they are produced


@dataclass(frozen=True)
class PipelineSettings:
    """Runtime knobs for the ask→brief pipeline."""

    query_timeout_s: int = 45
    max_result_rows: int = 10_000
    briefs_dir: Path = field(default_factory=lambda: Path("briefs"))
    allow_no_llm: bool = True  # degrade to the deterministic path, never crash a demo


@dataclass(frozen=True)
class Settings:
    llm: LlmSettings
    ch: ClickHouseSettings
    generator: GeneratorSettings
    pipeline: PipelineSettings = field(default_factory=PipelineSettings)

    def with_overrides(self, **kwargs) -> "Settings":
        """Return a copy with any section overridden (used by the loader/CLI)."""
        data = {"llm": self.llm, "ch": self.ch,
                "generator": self.generator, "pipeline": self.pipeline}
        for key, value in kwargs.items():
            section, _, attr = key.partition("__")
            if not attr:
                raise ValueError(f"override keys must look like ch__url, got {key!r}")
            obj = data[section]
            data[section] = type(obj)(**{**obj.__dict__, attr: value})
        return Settings(**data)


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Build Settings from the process environment (optionally loading a .env file)."""
    if load_dotenv is not None and env_file is not None and Path(env_file).exists():
        load_dotenv(env_file)

    return Settings(
        llm=LlmSettings(
            provider=os.getenv("PROVIDER", "gemini"),
            api_key=os.getenv("GEMINI_API_KEY", ""),
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            google_cloud_project=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
            google_cloud_location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        ),
        ch=ClickHouseSettings(
            url=os.getenv("CLICKHOUSE_URL", "http://localhost:8123"),
            user=os.getenv("CLICKHOUSE_USER", "default"),
            password=os.getenv("CLICKHOUSE_PASSWORD", ""),
            database=os.getenv("CLICKHOUSE_DATABASE", "studio"),
            transport=os.getenv("STUDIO_MIND_TRANSPORT", "mcp"),
        ),
        generator=GeneratorSettings(
            rows=_int(os.getenv("GENERATOR_ROWS"), 50_000_000),
            seed=_int(os.getenv("GENERATOR_SEED"), 20260826),
            batch_size=_int(os.getenv("GENERATOR_BATCH_SIZE"), 500_000),
            out_dir=Path(os.getenv("GENERATOR_OUT_DIR", str(Path("data") / "warehouse"))),
            load=_bool(os.getenv("GENERATOR_LOAD"), True),
        ),
        pipeline=PipelineSettings(
            query_timeout_s=_int(os.getenv("QUERY_TIMEOUT_S"), 45),
            max_result_rows=_int(os.getenv("MAX_RESULT_ROWS"), 10_000),
            briefs_dir=Path(os.getenv("BRIEFS_DIR", "briefs")),
            allow_no_llm=_bool(os.getenv("ALLOW_NO_LLM"), True),
        ),
    )


def get_settings() -> Settings:
    """Convenience: Settings from the default .env (or process env)."""
    return load_settings()
