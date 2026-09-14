# Profiles and platforms: what was discussed (2026-09-14)

A record of the conversation of 2026-09-13/14 about the two profiles, the
adapters, and the operating systems the local profile will run on. Nothing
here is approved unless it says so; the approved parts are in `AGENTS.md`
(two rules of 2026-09-14) and in roadmap 27.

## 1. Two profiles, one `app/`

The local app (Chainlit on the person's machine) and the deployed one
(Telegram on Modal) share `app/`. Since roadmap 27 the local app opens what
the deploy keeps shut: any folder as the conversation's root, reading
anywhere, a write outside the folder after a yes, localhost in the browser,
a command kept running in the background. Every one of these hangs on one
flag, `CapabilityRegistry(open=...)` / `create_agent(open=...)`, which only
the Chainlit app passes.

The human's worry, in two parts:

1. **A return to the deploy finds a harness rebuilt for one machine.** The
   deploy is not touched by local work until a `modal deploy`, which is a
   gate. What now stands between the two: the git tag `deployed` on the
   commit the running deploy was built from (`c4694b8`, the dpo
   measurement deploy of 2026-09-12, to be confirmed at the next deploy);
   `tests/test_profiles.py`, which builds the agent the way the deploy does
   and asserts the wiring is shut; and the rule that a local change names
   what it does deployed and in Telegram. The first thing the tag caught
   the same day: the background mode and its two tools were offered to the
   deployed model too, whose runner cannot keep a process; fixed
   (`f4c04f9`). A deploy is done as a gate with the deployed mini set and a
   Telegram turn, not as a push.
2. **UI problems designed into the core.** Chainlit reloads a conversation
   from our store (a page reload, a compact, a thread switch), Telegram
   never does: the chat lives at Telegram's. Two things were put into
   `app/` for Chainlit's reload without saying so: the `notes` table in the
   store contract (what the harness said, to show it again) and
   `ContentPart.path` (where a sent file lies, to show it again from disk).
   Both are additive and harmless deployed; both belong, by the rule below,
   in the Chainlit adapter. Moving them is a step on the human's word.

**Approved rule (AGENTS.md, 2026-09-14):** an interface's behaviour lives in
its adapter, `ui/telegram/` and `ui/chainlit_*`; `app/` takes only what is
the harness's or is needed the same way by every interface. A UI problem is
the adapter's question first; core only when it is a harness problem or a
universal one.

What is shared and was approved in words: context sizes in tokens
(small 128K, normal 256K, large 512K, clamped to the model's window), the
compact command's one line, `usage.cost` from OpenRouter and the agent's own
spend, the hidden attachment note. What is shared and was not asked about:
`notes`, `ContentPart.path` (above). Everything else of item 27 is local.

## 2. Platforms: Windows today, macOS and Linux later

The human: there will be a Mac and a local Linux; if the harness is not
built universally now, there will be trouble. Where the operating system
is asked about today:

| concern | Windows | Linux / macOS local | deployed (Linux container) |
|---|---|---|---|
| command boundary | restricted token + ACL (`app/tools/shell_windows.py`) | none; the brief says so | the container |
| background command | `RestrictedProcess` to a file, inherits the hidden console | `Popen` to a file, new session | not offered (the runner cannot keep a process) |
| kill a process tree | `taskkill /T` | `killpg` | n/a |
| event loop | Selector for psycopg, Proactor for stdio subprocesses; MCP on its own thread | default | default |
| browser | Chromium found by Windows paths | by their paths | the renderer image |
| temp, uploads, switches | `%TEMP%`, `.agent/` in the personal workspace | the same shape | the Volume |
| Cygwin tools under the boundary | die (ISS-0068) | n/a | n/a |

The known gap: ISS-0068, under the restricted token every Cygwin tool from
Git Bash on PATH (`ls`, `sh`, `find`, `cat`, `awk`, `grep`) dies at start;
`python`, `git`, `node`, `dir`, `type` run. Roadmap 27 step 3.

**Option discussed, not decided: one platform seam.** The harness knows the
operating system, but asks once, in one module, and hands out the answers:
a runner with the boundary, how to kill a tree, where temp is, which event
loop, where the browser is. The rest of `app/` asks for a runner or a temp
directory, never `sys.platform`. The references have this shape: Codex
holds three sandboxes (Seatbelt on macOS, bubblewrap or Landlock on Linux,
ACL on Windows) behind one interface. Today the checks sit as `if
sys.platform` branches in `shell.py` and in scripts; each new OS would mean
finding every branch. The proposed form has three parts:

1. The OS lives in a few named modules (the runner with its boundary, the
   browser launcher, the event loop choice, paths and temp); nothing else
   in `app/` reads `sys.platform`. A grep over the tree is the test.
2. A profile matrix in `docs/OPERATIONS_MAP.md`: rows local-windows,
   local-mac, local-linux, deployed; columns as in the table above. An
   empty cell is an honest "not built".
3. Checks by row: the mini set on every row that has a machine. Today only
   Windows has one; the deployed container stands in for Linux.

Cost: parts 1 and 2 are a day without a model; a macOS or Linux boundary is
a step of its own when the machine exists.

## 3. Commands: what the model may run

Asked and answered, not a decision. `run_command` is a plain shell in the
conversation's folder (`cmd` on Windows, `sh` elsewhere); any line runs.
The frames: writes only inside the folder (Windows enforces it, the others
say they cannot), a timeout (120 s default, `background=true` for a server
or a watcher), no stdin, a reduced environment without the agent's secrets
or venv, careful mode asking before each command. The brief steers file
work to `read_file`, `write_file`, `edit_file`, `list_files` rather than
`cat`, `echo`, `sed`, `ls`: a direction, not a ban, because the tools
return structure and `echo` through `cmd` breaks quotes and encodings.
The human: "возможно это надо обсудить".
