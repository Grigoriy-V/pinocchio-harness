"""One registry that turns a scoped grant into model-visible tools."""

from __future__ import annotations

from app.limits import DEFAULT_LIMITS, Limits

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from app.tools.base import Tool, Toolbox
from app.tools.browser import Pages, browser_tools
from app.tools.documents import document_tools
from app.tools.filesystem import filesystem_tools
from app.tools.patch import patch_tools
from app.tools.mcp import CAPABILITY_PREFIX, McpSessions, mcp_tools
from app.tools.presentation import presentation_tools
from app.tools.search import search_tools
from app.tools.shell import LocalRunner, Runner, shell_tools
from app.tools.web import web_fetch_tools, web_search_tools, web_view_tools

FILESYSTEM_READ = "filesystem.read"
FILESYSTEM_WRITE = "filesystem.write"
BROWSER_INSPECT = "browser.page"
DOCUMENTS_READ = "documents.read"
PRESENT_FILES = "presentation.files"
# Three, not one. They differ in what they cost and in what they let run: search
# spends a provider's credit, fetching spends nothing and executes nothing, and
# viewing runs a page's own JavaScript. A grant that could only say "the web"
# could not withhold the expensive one.
WEB_SEARCH = "web.search"
WEB_FETCH = "web.fetch"
WEB_VIEW = "web.view"
# Running a command in the workspace. Where it runs is the registry's `runner`,
# chosen by the profile; the grant only says whether the tool is there.
SHELL_RUN = "shell.run"
DEFAULT_CAPABILITIES = (
    FILESYSTEM_READ,
    FILESYSTEM_WRITE,
    BROWSER_INSPECT,
    DOCUMENTS_READ,
    PRESENT_FILES,
    WEB_SEARCH,
    WEB_FETCH,
    WEB_VIEW,
    SHELL_RUN,
)


@dataclass(frozen=True)
class Capability:
    name: str
    build: Callable[[Path], list[Tool]]


@dataclass(frozen=True)
class CapabilityGrant:
    root: Path
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve())
        object.__setattr__(self, "capabilities", tuple(dict.fromkeys(self.capabilities)))

    def allows(self, capability: str) -> bool:
        return capability in self.capabilities


def _filesystem_read(root: Path, open_reads: bool, limits: Limits = DEFAULT_LIMITS) -> list[Tool]:
    """The file tools that change nothing: reading, searching, finding."""

    return [
        *(tool for tool in filesystem_tools(root, open_reads=open_reads, limits=limits) if not tool.mutates),
        *search_tools(root, open_reads=open_reads, limits=limits),
    ]


def _filesystem_write(root: Path, open_reads: bool, limits: Limits = DEFAULT_LIMITS) -> list[Tool]:
    """The file tools that change the tree: write, edit, a patch."""

    return [
        *(tool for tool in filesystem_tools(root, open_reads=open_reads, limits=limits) if tool.mutates),
        *patch_tools(root, open_reads=open_reads),
    ]


class CapabilityRegistry:
    """Validate grants and expose only the tools they allow."""

    def __init__(
        self,
        workspace: Path,
        capabilities: Iterable[Capability] | None = None,
        runner: Runner | None = None,
        mcp: McpSessions | None = None,
        open: bool = False,
        limits: Limits = DEFAULT_LIMITS,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        # Every bound the tools it builds take (roadmap 31): the settings'
        # numbers, put on the budget by the runtime once the window is known.
        self.limits: Limits = limits
        # The person's own machine (roadmap 27, 2026-09-13): a grant may name
        # any folder on it, reading reaches anywhere, a write outside the
        # folder asks. Deployed, off: several people share one Volume.
        self.open = open
        if not self.workspace.is_dir():
            raise ValueError(f"the workspace {workspace} is not a directory")
        self.runner: Runner = runner if runner is not None else LocalRunner()
        # The connections to configured MCP servers (roadmap 26), kept here
        # like the runner: one per process, not per toolbox.
        self.mcp = mcp
        # The open page, kept between the calls of one turn whichever toolbox
        # they come through (a toolbox is built per thread, and more than once).
        self.pages = Pages(open_addresses=open)
        configured = (
            capabilities
            if capabilities is not None
            else (
                Capability(FILESYSTEM_READ, lambda root: _filesystem_read(root, self.open, self.limits)),
                Capability(FILESYSTEM_WRITE, lambda root: _filesystem_write(root, self.open, self.limits)),
                Capability(BROWSER_INSPECT, lambda root: browser_tools(root, pages=self.pages, limits=self.limits)),
                Capability(DOCUMENTS_READ, lambda root: document_tools(root, open_reads=self.open, limits=self.limits)),
                Capability(PRESENT_FILES, lambda root: presentation_tools(root, open_reads=self.open)),
                Capability(WEB_SEARCH, web_search_tools),
                Capability(WEB_FETCH, lambda root: web_fetch_tools(root, open=self.open, limits=self.limits)),
                Capability(WEB_VIEW, lambda root: web_view_tools(root, limits=self.limits)),
                Capability(SHELL_RUN, lambda root: shell_tools(root, self.runner, limits=self.limits)),
            )
        )
        self._capabilities = {capability.name: capability for capability in configured}
        for server in (mcp.names if mcp is not None else ()):
            name = CAPABILITY_PREFIX + server
            self._capabilities[name] = Capability(name, lambda root, s=server: mcp_tools(mcp, s))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._capabilities)

    @property
    def mcp_names(self) -> tuple[str, ...]:
        """The capabilities that stand for configured MCP servers."""

        return tuple(name for name in self._capabilities if name.startswith(CAPABILITY_PREFIX))

    def grant(
        self,
        root: str | Path | None = None,
        capabilities: Iterable[str] = DEFAULT_CAPABILITIES,
    ) -> CapabilityGrant:
        supplied = Path(root) if root is not None else self.workspace
        allowed_root = (
            supplied.resolve()
            if supplied.is_absolute()
            else (self.workspace / supplied).resolve()
        )
        if (
            not self.open
            and allowed_root != self.workspace
            and self.workspace not in allowed_root.parents
        ):
            raise PermissionError("capability grant root is outside the workspace")
        if not allowed_root.is_dir():
            raise ValueError("capability grant root is not a directory")
        requested = tuple(dict.fromkeys(capabilities))
        unknown = [name for name in requested if name not in self._capabilities]
        if unknown:
            raise ValueError(f"unknown capabilities: {', '.join(unknown)}")
        return CapabilityGrant(allowed_root, requested)

    def toolbox(
        self,
        grant: CapabilityGrant,
        extra_tools: Iterable[Tool] = (),
        ask_for_changes: bool = False,
    ) -> Toolbox:
        checked = self.grant(grant.root, grant.capabilities)
        tools: list[Tool] = []
        for name in checked.capabilities:
            tools.extend(self._capabilities[name].build(checked.root))
        tools.extend(extra_tools)
        names = [tool.name for tool in tools]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate tool names: {', '.join(duplicates)}")
        return Toolbox(tools, ask_for_changes=ask_for_changes)
