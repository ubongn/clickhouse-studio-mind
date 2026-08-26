"""M4 cloud validation: official mcp-clickhouse against ClickHouse Cloud."""
import time

from studio_mind.config import get_settings
from studio_mind.mcp_transport import McpClient

s = get_settings()
print(f"transport={s.ch.transport} url={s.ch.url} db={s.ch.database}")
t0 = time.time()
c = McpClient.from_settings(s)
print(f"session ready in {time.time()-t0:.1f}s  tool={c._tool} list_tool={c._list_tool}")

r = c.query("SELECT 1 AS ok, version() AS v")
print("SELECT 1 ->", r.result_rows)

tabs = c.list_tables()
print(f"list_tables -> {len(tabs)} tables")
for t in tabs:
    print("   ", t if not isinstance(t, dict) else {k: t.get(k) for k in ('name', 'total_rows') if k in t})

counts = c.query(
    "SELECT 'viewing_events' AS t, count() AS n FROM studio.viewing_events "
    "UNION ALL SELECT 'users', count() FROM studio.users "
    "UNION ALL SELECT 'titles', count() FROM studio.titles "
    "UNION ALL SELECT 'episodes', count() FROM studio.episodes"
)
print("row counts ->", counts.rows_as_dicts)

stats = c.query_stats()
print("query_stats (last QueryFinish via MCP) ->", stats)

# write-refusal proof: the server must refuse DDL
try:
    c.query("CREATE TABLE studio.should_not_exist (x UInt8)")
    print("WRITE REFUSAL: FAILED (write went through!)")
except Exception as e:
    print("write refused inside official server ->", str(e)[:120])

c.close()
print("closed cleanly")
