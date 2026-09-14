# Pinocchio Harness

The same harness that answers in Telegram, running on this machine: one
loop, one model behind an OpenAI-compatible endpoint, GLM through
OpenRouter by default. Conversations, long-term facts and the workspace
survive a restart; older turns are folded rather than dropped.

Commands, from the composer's menu or typed: `/workspace <path>` names the
folder this conversation works in, `/plan on|off` keeps a task list for
longer work, `/mode full|careful` decides whether changes ask first,
`/context small|normal|large` sets what the next request is made of, and
`/compact` folds the older part of this conversation now.

The assistant reads any path on this machine, writes inside the folder you
named, and asks before writing anywhere else. The status card beside the
composer opens on its button and shows the context, the cost and the
credits left. A file you send is not uploaded anywhere: it is kept on this
machine and the assistant is given its path.
