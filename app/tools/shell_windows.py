"""A command that can write only inside the workspace, on native Windows.

The one property every reference enforces — Codex's writable roots, Claude
Code's sandbox on macOS and Linux — is that a command may write to the
workspace and its temp and nowhere else, whatever the command is. Native
Windows has no Seatbelt or bubblewrap; the mechanism DeepSeek Harness's
`sandbox-windows-acl` uses without administrator rights, and this follows, is
a **write-restricted token**: every write access check is passed twice, once
as the user and once against the restricting identities, so the process can
write only where one of those identities has been granted write access. The
workspace is granted to the well-known `RESTRICTED` identity with
inheritance, and nothing else is: the machine's Python, the person's profile,
the registry all refuse. Reading and the network are untouched.

What it took to make it start, all measured on 2026-09-04 and none of it in
the API reference:

- the restricting list must keep the **logon SID and Everyone**, or DLL
  initialization dies with `STATUS_DLL_INIT_FAILED`; so does DeepSeek's, and
  it is the known partial case: a place Everyone may write to, such as
  `C:\\Users\\Public`, stays writable;
- `LUA_TOKEN` must not be set: it makes Administrators deny-only, and on a
  machine where the person's directories are theirs through that group the
  command cannot even list its workspace;
- the token's **default DACL** must admit the restricting identities, or a
  process the command starts (everything `cmd` runs) fails the same way;
- the command must **inherit a console** rather than get one of its own —
  a new console, hidden or not, fails at initialization, and `cmd` without
  any console hands its children dead standard handles even under an
  unrestricted token — so the agent process allocates a hidden console once
  if it has none; and the output goes to a file in the workspace's temp,
  not a pipe, because a pipe carries no ACL the restricted grandchild
  could pass.

Windows-only by construction; `app/tools/shell.py` uses it when it imports.
`DECISIONS.md` 2026-09-04.
"""

from __future__ import annotations

import ctypes
import subprocess
import threading
from pathlib import Path

import pywintypes
import win32api
import win32con
import win32event
import win32file
import win32process
import win32security

RESTRICTED_SID_TEXT = "S-1-5-12"
EVERYONE_SID_TEXT = "S-1-1-0"
DISABLE_MAX_PRIVILEGE = 0x1
WRITE_RESTRICTED = 0x8
CREATE_UNICODE_ENVIRONMENT = 0x00000400
SE_GROUP_LOGON_ID = 0xC0000000

_granted: set[Path] = set()
_lock = threading.Lock()
_console_checked = False


def ensure_console() -> None:
    """A console for the agent process, hidden, if it has none (see the module docstring)."""

    global _console_checked
    with _lock:
        if _console_checked:
            return
        kernel32 = ctypes.windll.kernel32
        if kernel32.AllocConsole():
            window = kernel32.GetConsoleWindow()
            if window:
                ctypes.windll.user32.ShowWindow(window, 0)
        _console_checked = True


def grant_workspace(workspace: Path) -> None:
    """Let the restricted identity write in the workspace and everything under it.

    `icacls` on a directory the person owns needs no elevation; the
    inheritable entry propagates to what exists and to what is made later.
    Once per process per workspace.
    """

    resolved = Path(workspace).resolve()
    with _lock:
        if resolved in _granted:
            return
        subprocess.run(
            ["icacls", str(resolved), "/grant", f"*{RESTRICTED_SID_TEXT}:(OI)(CI)M", "/Q"],
            check=True,
            capture_output=True,
            timeout=60,
        )
        _granted.add(resolved)


def restricted_token():
    """The caller's own token, write-restricted; see the module docstring for each choice."""

    own = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_ALL_ACCESS)
    restricted = win32security.ConvertStringSidToSid(RESTRICTED_SID_TEXT)
    everyone = win32security.ConvertStringSidToSid(EVERYONE_SID_TEXT)
    logon = next(
        sid
        for sid, attributes in win32security.GetTokenInformation(own, win32security.TokenGroups)
        if attributes & SE_GROUP_LOGON_ID == SE_GROUP_LOGON_ID
    )
    token = win32security.CreateRestrictedToken(
        own,
        DISABLE_MAX_PRIVILEGE | WRITE_RESTRICTED,
        [],
        [],
        [(logon, 0), (everyone, 0), (restricted, 0)],
    )
    dacl = win32security.GetTokenInformation(token, win32security.TokenDefaultDacl) or win32security.ACL()
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, logon)
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, restricted)
    win32security.SetTokenInformation(token, win32security.TokenDefaultDacl, dacl)
    return token


class RestrictedProcess:
    """Enough of `subprocess.Popen` for the runner: `pid`, `poll`, `kill`, `communicate`."""

    def __init__(self, command_line: str, cwd: Path, env: dict[str, str], output: Path) -> None:
        """`command_line` is the whole line for `CreateProcess`, shell included
        (`app/tools/shell.py::command_line`)."""

        ensure_console()
        self._output = output
        inheritable = pywintypes.SECURITY_ATTRIBUTES()
        inheritable.bInheritHandle = True
        out = win32file.CreateFile(
            str(output),
            win32con.GENERIC_WRITE,
            win32con.FILE_SHARE_READ,
            inheritable,
            win32con.CREATE_ALWAYS,
            0,
            None,
        )
        devnull = win32file.CreateFile(
            "NUL", win32con.GENERIC_READ, 0, inheritable, win32con.OPEN_EXISTING, 0, None
        )
        startup = win32process.STARTUPINFO()
        startup.dwFlags = win32con.STARTF_USESTDHANDLES
        startup.hStdInput = devnull
        startup.hStdOutput = out
        startup.hStdError = out
        try:
            handle, thread, self.pid, _ = win32process.CreateProcessAsUser(
                restricted_token(),
                None,
                command_line,
                None,
                None,
                True,
                win32con.CREATE_NEW_PROCESS_GROUP | CREATE_UNICODE_ENVIRONMENT,
                env,
                str(cwd),
                startup,
            )
        finally:
            out.Close()
            devnull.Close()
        thread.Close()
        self._handle = handle
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is None:
            if win32event.WaitForSingleObject(self._handle, 0) == win32event.WAIT_OBJECT_0:
                self.returncode = win32process.GetExitCodeProcess(self._handle)
        return self.returncode

    def kill(self) -> None:
        try:
            win32process.TerminateProcess(self._handle, 1)
        except pywintypes.error:
            pass

    def _read(self) -> bytes:
        try:
            return self._output.read_bytes()
        except OSError:
            return b""

    def communicate(self, input: bytes | None = None, timeout: float | None = None) -> tuple[bytes, None]:
        wait = win32event.INFINITE if timeout is None else int(timeout * 1000)
        if win32event.WaitForSingleObject(self._handle, wait) != win32event.WAIT_OBJECT_0:
            raise subprocess.TimeoutExpired("run_command", timeout or 0, output=self._read())
        self.returncode = win32process.GetExitCodeProcess(self._handle)
        data = self._read()
        try:
            self._output.unlink()
        except OSError:
            pass
        return data, None
