"""Print one thread's rows: role, tool calls, parts and sizes, the head of the text.

    python tools/show_thread.py <thread_id> [head_chars]

Opens whatever the application would open, like `show_run.py`: the local
SQLite store by default, the deployed database when `AGENT_DATABASE_URL` is
set. Prints row states only — never a connection string. A media part is
shown as its kind and byte count, not its bytes (ISS-0066 was found with
this). Read-only; never migrates, never starts anything.
"""

import sys

from app.config import AgentSettings
from app.memory.open import open_store

thread = sys.argv[1]
head = int(sys.argv[2]) if len(sys.argv) > 2 else 400
store = open_store(AgentSettings())
try:
    print("count", store.message_count(thread))
    summary, through = store.summary(thread)
    print("summary through", through, ":", (summary or "")[:300])
    for i, m in enumerate(store.messages(thread)):
        text = ""
        parts = []
        content = m.content
        if isinstance(content, str):
            text = content
        else:
            for p in content:
                parts.append(f"{p.kind}:{p.name or ''}:{len(p.data or b'')}b")
                if p.text:
                    text += p.text
        calls = getattr(m, "tool_calls", None) or []
        names = ",".join(c.get("name", "?") if isinstance(c, dict) else str(c) for c in calls)
        flag = " FAILURE" if getattr(m, "failure", None) else ""
        one = " ".join(text.split())
        print(f"--- {i:3} {m.role}{flag} len={len(text)} calls=[{names}] parts={parts}")
        print("   ", one[:head])
finally:
    store.close()
