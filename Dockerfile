# ClickHouse Studio Mind — Cloud Run container.
#
# The image contains the full runtime path: the pipeline, the official
# mcp-clickhouse server (spawned as a stdio subprocess by the transport),
# and the HTTP service (studio_mind.server). No secrets are baked in —
# ClickHouse + Vertex config arrives as Cloud Run env vars / ADC.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install the package first so Docker layer caching skips deps when only
# application code changes.
COPY pyproject.toml README.md LICENSE ./
COPY studio_mind ./studio_mind
RUN pip install --no-cache-dir .

# Data generator + seed schema stay out of the runtime image; the warehouse
# is loaded once by `python -m data.generate` (dev/CI), not per-request.

EXPOSE 8080
ENV PORT=8080

# Gunicorn-free single process: uvicorn with one worker keeps the persistent
# MCP session model simple; Cloud Run scales by container, not workers.
CMD ["python", "-m", "studio_mind.server"]
