# Running ClickHouse for Studio Mind

Studio Mind needs a real ClickHouse to talk to — it introspects the schema, executes
generated SQL, and turns query results into cited evidence. The client is fully
URL-driven (`CLICKHOUSE_URL`), so any ClickHouse works. Pick one of the paths below.

## Why there is no bundled engine

ClickHouse does not publish native Windows binaries, and this project's primary
development machine is Windows without Docker/WSL. Instead of faking an engine,
Studio Mind treats ClickHouse as infrastructure and gives you three supported ways
to get one, plus a CI path that always works:

| Path | How | Best for |
|---|---|---|
| **ClickHouse Cloud** (recommended) | free developer service at `console.clickhouse.cloud` | local dev + hosted demo — zero install |
| Docker | `docker run -d -p 8123:8123 --name studio-ch clickhouse/clickhouse-server:latest` | any machine with Docker |
| Native Linux / WSL | official `deb`/`tgz` packages from `clickhouse.com` | Linux workstations |
| GitHub Actions | `services: clickhouse: clickhouse/clickhouse-server` in CI | schema + loader + integration tests, no install at all |

Every path produces the same endpoint shape:

```
CLICKHOUSE_URL=http://localhost:8123        # or https://<id>.<region>.aws.clickhouse.cloud:8443
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=
CLICKHOUSE_DATABASE=studio
```

## ClickHouse Cloud, step by step

1. Sign up at [console.clickhouse.cloud](https://console.clickhouse.cloud) (free
   developer service, no credit card).
2. Create a **Development** service in any region. Note the HTTPS endpoint and the
   `default` user password it shows you once.
3. Put the connection into `.env` (see `.env.example`).
4. Build the warehouse:

   ```bash
   python -m data.generate --rows 50000000    # writes Parquet batches + loads
   ```

The loader streams batches over HTTP — no bulk S3 upload needed at this scale.

## Docker, step by step

```bash
docker run -d --name studio-ch -p 8123:8123 -p 9000:9000 \
  clickhouse/clickhouse-server:latest
cp .env.example .env                          # defaults already point at localhost:8123
python -m data.generate
```

## GitHub Actions (how CI runs the warehouse)

`.github/workflows/ci.yml` starts a `clickhouse/clickhouse-server` service container,
applies the schema, generates a small dataset (200k rows) and runs the full test
suite — including the SQL-level integration tests. If you want to see the warehouse
live without installing anything, open the Actions tab on any commit.

## How the agent reaches ClickHouse at runtime

By default the evidence pipeline routes every query through the official
[`mcp-clickhouse`](https://github.com/ClickHouse/mcp-clickhouse) server (spawned as
a stdio subprocess, driven over the MCP protocol — read-only `SELECT` tooling only).
Set `STUDIO_MIND_TRANSPORT=http` to call ClickHouse directly via `clickhouse-connect`
instead (used by the loader and by tests). Both transports speak to the same
`CLICKHOUSE_URL`.

## Verifying a setup

```bash
studio-mind doctor        # checks URL reachability, credentials, schema state, row counts
```
