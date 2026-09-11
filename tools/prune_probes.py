"""Delete the probe users' conversations and checkpoints after an export (roadmap 24).

    python tools/prune_probes.py            counts only
    python tools/prune_probes.py --apply    delete and vacuum: a gate, never during a run


Probe threads are `chat-*` (the default probe) and `loop-live-p*:chat-*`.
Telemetry rows (turn_runs, trace_events) stay: small, and what the export
and the reports point at. `--apply` deletes; without it, counts only.
"""

import sys

import psycopg
from psycopg.rows import dict_row

from app.config import AgentSettings

APPLY = "--apply" in sys.argv
s = AgentSettings()
schema = s.database_schema
PROBE = "(thread_id LIKE 'chat-%%' OR thread_id LIKE 'loop-live-p%%:chat-%%')"
with psycopg.connect(s.database_url, row_factory=dict_row) as conn:
    cur = conn.cursor()
    plan = [
        ("public.checkpoint_blobs", PROBE, "blob"),
        ("public.checkpoint_writes", PROBE, "blob"),
        ("public.checkpoints", PROBE, None),
        (f"{schema}.messages", PROBE, "content"),
        (f"{schema}.threads", "(id LIKE 'chat-%%' OR id LIKE 'loop-live-p%%:chat-%%')", None),
    ]
    for table, where, col in plan:
        size = f", pg_size_pretty(sum(octet_length({col}))) AS data" if col else ""
        cur.execute(f"SELECT count(*) AS n{size} FROM {table} WHERE {where}")
        r = cur.fetchone()
        print(f"  {table:28} {r['n']:7} rows" + (f"  {r['data']}" if col else ""))
    cur.execute(f"SELECT count(*) AS n FROM {schema}.facts WHERE user_id LIKE 'loop-live%%'")
    print(f"  {schema + '.facts (probe)':28} {cur.fetchone()['n']:7} rows")
    if not APPLY:
        conn.rollback()
        print("dry run; nothing changed")
        sys.exit(0)
    for table, where, _ in plan:
        cur.execute(f"DELETE FROM {table} WHERE {where}")
        print(f"  deleted {cur.rowcount} from {table}")
    cur.execute(f"DELETE FROM {schema}.facts WHERE user_id LIKE 'loop-live%%'")
    print(f"  deleted {cur.rowcount} probe facts")
    conn.commit()
    conn.autocommit = True
    for t in ("public.checkpoint_blobs", "public.checkpoint_writes", "public.checkpoints", f"{schema}.messages"):
        cur.execute(f"VACUUM FULL {t}")
    cur.execute("SELECT pg_size_pretty(pg_database_size(current_database())) AS total")
    print("database after:", cur.fetchone()["total"])
