"""Keep each thread's latest checkpoint (and what it references), delete the rest.

    python tools/prune_checkpoints.py            dry run: what would go
    python tools/prune_checkpoints.py --apply    delete, then VACUUM FULL the three tables

ISS-0067: LangGraph's Postgres saver keeps every checkpoint version of every
thread and the harness reads only the latest, so the deployed database fills
with dead versions (368 of 394 MB on 2026-09-11). Until a mechanism prunes,
this is run by hand. `--apply` deletes from a populated database: a human
gate every time, and never while a turn is running (a running turn's
pending writes belong to a checkpoint this would keep, but do not rely on
it). Reads `AGENT_DATABASE_URL` through the settings; prints no URL.
"""

import sys

import psycopg
from psycopg.rows import dict_row

from app.config import AgentSettings

APPLY = "--apply" in sys.argv
s = AgentSettings()
with psycopg.connect(s.database_url, row_factory=dict_row) as conn:
    cur = conn.cursor()
    # The latest checkpoint per thread (one namespace in this database).
    cur.execute(
        """
        CREATE TEMP TABLE keep AS
        SELECT DISTINCT ON (thread_id, checkpoint_ns) thread_id, checkpoint_ns, checkpoint_id,
               checkpoint->'channel_versions' AS versions
        FROM public.checkpoints
        ORDER BY thread_id, checkpoint_ns, checkpoint_id DESC
        """
    )
    cur.execute("SELECT count(*) AS n FROM keep")
    print("threads with a checkpoint:", cur.fetchone()["n"])
    cur.execute(
        """
        SELECT count(*) AS n FROM public.checkpoints c
        WHERE NOT EXISTS (SELECT 1 FROM keep k WHERE k.thread_id=c.thread_id AND k.checkpoint_ns=c.checkpoint_ns AND k.checkpoint_id=c.checkpoint_id)
        """
    )
    print("checkpoint rows to delete:", cur.fetchone()["n"])
    cur.execute(
        """
        SELECT count(*) AS n, pg_size_pretty(coalesce(sum(octet_length(blob)),0)) AS data FROM public.checkpoint_writes w
        WHERE NOT EXISTS (SELECT 1 FROM keep k WHERE k.thread_id=w.thread_id AND k.checkpoint_ns=w.checkpoint_ns AND k.checkpoint_id=w.checkpoint_id)
        """
    )
    r = cur.fetchone(); print("write rows to delete:", r["n"], r["data"])
    cur.execute(
        """
        SELECT count(*) AS n, pg_size_pretty(coalesce(sum(octet_length(b.blob)),0)) AS data FROM public.checkpoint_blobs b
        WHERE NOT EXISTS (
            SELECT 1 FROM keep k
            WHERE k.thread_id=b.thread_id AND k.checkpoint_ns=b.checkpoint_ns
              AND k.versions ? b.channel AND k.versions->>b.channel = b.version)
        """
    )
    r = cur.fetchone(); print("blob rows to delete:", r["n"], r["data"])
    cur.execute(
        """
        SELECT count(*) AS n, pg_size_pretty(coalesce(sum(octet_length(b.blob)),0)) AS data FROM public.checkpoint_blobs b
        WHERE EXISTS (
            SELECT 1 FROM keep k
            WHERE k.thread_id=b.thread_id AND k.checkpoint_ns=b.checkpoint_ns
              AND k.versions ? b.channel AND k.versions->>b.channel = b.version)
        """
    )
    r = cur.fetchone(); print("blob rows kept:", r["n"], r["data"])
    if not APPLY:
        conn.rollback()
        print("dry run; nothing changed")
        sys.exit(0)
    cur.execute(
        """
        DELETE FROM public.checkpoint_blobs b
        WHERE NOT EXISTS (
            SELECT 1 FROM keep k
            WHERE k.thread_id=b.thread_id AND k.checkpoint_ns=b.checkpoint_ns
              AND k.versions ? b.channel AND k.versions->>b.channel = b.version)
        """
    )
    print("deleted blobs:", cur.rowcount)
    cur.execute(
        """
        DELETE FROM public.checkpoint_writes w
        WHERE NOT EXISTS (SELECT 1 FROM keep k WHERE k.thread_id=w.thread_id AND k.checkpoint_ns=w.checkpoint_ns AND k.checkpoint_id=w.checkpoint_id)
        """
    )
    print("deleted writes:", cur.rowcount)
    cur.execute(
        """
        DELETE FROM public.checkpoints c
        WHERE NOT EXISTS (SELECT 1 FROM keep k WHERE k.thread_id=c.thread_id AND k.checkpoint_ns=c.checkpoint_ns AND k.checkpoint_id=c.checkpoint_id)
        """
    )
    print("deleted checkpoints:", cur.rowcount)
    conn.commit()
    conn.autocommit = True
    for t in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
        cur.execute(f"VACUUM FULL public.{t}")
    cur.execute("SELECT pg_size_pretty(pg_database_size(current_database())) AS total")
    print("database after:", cur.fetchone()["total"])
