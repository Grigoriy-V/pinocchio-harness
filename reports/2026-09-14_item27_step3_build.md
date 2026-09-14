# Roadmap 27 step 3, build: the coding tools, checked against the references

**Date:** 2026-09-14. **Research it builds on:**
`reports/2026-09-14_item27_step3_references.md` (approved 2026-09-14, all
seven points; `list_files` removed on the human's question the same day).
**Status:** built and tested offline; the live acceptance (a local coding
mini set beside the deployed numbers) is a priced run and waits for the
human's word.

## 1. What was built, against §3 of the research

| research §3 | built | where |
|---|---|---|
| 3.1 `read_file` by lines, numbered, `offset`/`limit`, footer with the next offset, long lines cut, a page under the executor's cap | yes: `N: text`, 1-based, default 500 lines, `LINE_CHARS` 2,000, `PAGE_CHARS` 30,000 (constants until roadmap 31) | `app/tools/filesystem.py::numbered_page` |
| 3.2 `search_files` (regex, glob, modes content/files/count, limit/offset, ignore_case, multiline, `.gitignore`), `find_files` (glob, newest first, paged); ripgrep when present, Python walk otherwise, one contract | yes; `.gitignore` through `git ls-files --cached --others --exclude-standard`; a bare `find_files` pattern lists one directory, `**/` reaches down; `list_files` gone | `app/tools/search.py` |
| 3.3 `edit_file` `replace_all` and line numbers; `apply_patch` V4A, several files and hunks, exact then whitespace-forgiven match, all or nothing, unmatched lines quoted | yes; `*** Move to:` and `*** End of File` handled; a fenced patch accepted | `app/tools/filesystem.py::_edit_file`, `app/tools/patch.py` |
| 3.4 a write returns `+a -b lines`; the full diff is the adapter's | yes: `overwrote path (+a -b lines, now N lines)`, `created path (N lines)`, `edited path: replaced n at lines …` | `filesystem.py::_write_file` |
| 3.5 descriptions without "use this instead of" and without the fence/argument-order coaching; the brief names OS, shell and Python | yes: five sentences gone; `run_command` says which shell; the runner's `where` says PowerShell 5.1's dialect (`;` not `&&`, `python` not `python3`); an offline test refuses "use this instead of" in any wired tool | `filesystem.py`, `shell.py`, `tests/test_tool_contracts.py` |
| 3.6 `fetch_page` under the local `open` flag | yes: `check_destination(open=True)` lifts the public-address and port rules, the scheme rule stands; deployed unchanged | `app/web.py`, `app/tools/web.py`, `app/tools/capabilities.py` |
| 3.7 option A: PowerShell as the Windows shell under the same token | yes: `pwsh` when installed else `powershell`, the command carried as base64 into a script block so quotes are parsed once and a syntax error is text, errors and progress joined to stdout as text, the last native exit code or 1 after an error record; `cmd` no longer on the route | `shell.py::powershell_argv`, `command_line`; `shell_windows.RestrictedProcess` takes the whole line |
| 3.7 option B (Cygwin under the token) | not built, recorded as not started | `ROADMAP.md` |

Also from the research's §2: the read/write capability split by what a tool
does (`Tool.mutates`) rather than by list position (audit §D item 68).

## 2. What it does to the deployed profile and to Telegram

- Deployed: the same six file tools appear, all inside the root
  (`open=False`); `search_files` runs the Python engine (no ripgrep in the
  container); `fetch_page` stays public-only; `sh` stays the shell; the
  brief's shell sentence is the container's as before.
  `tests/test_profiles.py` still asserts the wiring shut. The preflight's
  filesystem probe lists with `find_files`.
- Telegram: three labels in `TOOL_ACTIVITY` (`find_files`, `search_files`,
  `apply_patch`); nothing else in the adapter. `scripts/loop_live.py`'s
  scenario I fakes `find_files` where it faked `list_files`.
- Not deployed: the next deploy is a gate, with the deployed mini set and a
  Telegram turn, as `AGENTS.md` says.

## 3. Checks run

- `tests/test_search_tools.py` (14), `tests/test_apply_patch.py` (8), new.
- Updated: `test_tools`, `test_capabilities`, `test_working_folder`,
  `test_tool_contracts`, `test_preflight`, `test_tool_outcomes`,
  `test_agent_graph`, `test_repeated_failure`, `test_turn_resume`,
  `test_telegram_adapter`, `test_run_command` (commands written for
  PowerShell where the test is Windows-only; `PY` calls a quoted path
  with `&` on Windows).
- PowerShell under the restricted token, by hand, no model: `echo`,
  `Get-ChildItem`, `python --version`, a write outside the folder refused
  (`Access to the path … is denied`, exit 1), `exit 3` → 3, a Python
  `sys.exit(4)` → 4, `python3` → "not recognized" as text, exit 1.
- The affected suites: 247 passed. The whole offline suite: see the commit
  message of this step.

## 4. Limitations and what is left

- The live acceptance has not run: a local coding mini set (tree search,
  multi-file patch, a long file by lines, a dev server fetched on
  localhost, a PowerShell command) beside the deployed numbers is a priced
  run and waits for the human's word. ISS-0074's fetch half and the
  PowerShell route are therefore seen offline only.
- A cmdlet's non-terminating error gives exit 1 through `$Error.Count`;
  a script that writes an error record and then succeeds natively keeps the
  native code. Codex and Claude Code carry the same ambiguity.
- The page and search constants (`DEFAULT_LINES`, `PAGE_CHARS`,
  `LINE_CHARS`, `PREVIEW_CHARS`, `DEFAULT_LIMIT`) are written, not
  derived; roadmap 31.
- ripgrep is not on this machine, so the ripgrep engine is exercised only
  through its argument line; the Python engine is what the tests run.
- `run_command`'s own truncation and its 600 s clamp are untouched here
  (roadmap 31).
