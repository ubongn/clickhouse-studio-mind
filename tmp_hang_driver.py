"""Reproduce the stdio-server hang in background so py-spy can dump stacks."""
import os, sys, time, json
from dotenv import load_dotenv; load_dotenv()
from studio_mind.config import get_settings
from studio_mind.mcp_transport import McpClient

s = get_settings()
c = McpClient.from_settings(s)
print('driver pid', os.getpid(), 'session ready; tool =', c._tool, flush=True)

# find the mcp-clickhouse child pid (python -m mcp_clickhouse.main)
import subprocess
out = subprocess.run(['wmic', 'process', 'where',
    "commandline like '%mcp_clickhouse.main%'",
    'get', 'processid,commandline'], capture_output=True, text=True).stdout
pids = [int(ln.split()[-1]) for ln in out.splitlines() if ln.strip() and ln.strip()[0].isdigit()]
print('child pids:', pids, flush=True)

t0 = time.time()
try:
    r = c.query('SELECT 1 AS ok, version() AS v')
    print('QUERY OK', r.result_rows, '%.1fs' % (time.time()-t0), flush=True)
    c.close()
except Exception as e:
    print('QUERY FAILED after %.1fs:' % (time.time()-t0), str(e)[:200], flush=True)
    # leave process alive for py-spy
    time.sleep(120)
