# Copyright 2026 Phil Pennock — see LICENSE file.

"""Interactive REPL for exercising an MCP server."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import commands
from .color import bold, dim, red, yellow
from .completion import (
    CompletionState,
    readline_info,
    setup_readline,
)
from .diagnostics import Finding, Level
from .parse import parse_args
from .protocol import McpError, RequestCancelled
from .render import render_error, render_response
from .sanitize import sanitize
from .transport import SessionExpiredError, TransportError
from .transport.params import param_headers
from .versions import Feature

if TYPE_CHECKING:
    from .protocol import McpSession

# Lists the REPL caches: kind → (method, result key, capability that advertises it).
LISTS: dict[str, tuple[str, str, str]] = {
    "tools": ("tools/list", "tools", "tools"),
    "resources": ("resources/list", "resources", "resources"),
    "templates": ("resources/templates/list", "resourceTemplates", "resources"),
    "prompts": ("prompts/list", "prompts", "prompts"),
}

_LIST_CHANGED = {
    "notifications/tools/list_changed": ("tools",),
    "notifications/resources/list_changed": ("resources", "templates"),
    "notifications/prompts/list_changed": ("prompts",),
}


@dataclass
class SessionCache:
    """Server lists, fetched lazily and refreshed on list_changed."""

    lists: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    stale: set[str] = field(default_factory=set)
    completion: CompletionState = field(default_factory=CompletionState)

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self.lists.get("tools", [])

    @property
    def resources(self) -> list[dict[str, Any]]:
        return self.lists.get("resources", [])

    @property
    def resource_templates(self) -> list[dict[str, Any]]:
        return self.lists.get("templates", [])

    @property
    def prompts(self) -> list[dict[str, Any]]:
        return self.lists.get("prompts", [])

    def rebuild_completion(self) -> None:
        self.completion = CompletionState.from_tools(
            self.tools, prompts=self.prompts, resources=self.resources
        )

    @classmethod
    def build(cls, session: McpSession) -> SessionCache:
        cache = cls()
        cache.fetch_advertised(session)
        return cache

    def fetch_advertised(self, session: McpSession) -> None:
        """Fetch what the server advertises.  Tools are always tried: many
        servers forget the capability, and a tool-less REPL is no use."""
        caps = session.server_capabilities
        self.lists.clear()
        for kind, (_, _, cap) in LISTS.items():
            if kind == "tools" or cap in caps:
                self.fetch(session, kind, quiet=kind == "tools" and cap not in caps)
        self.stale.clear()
        self.rebuild_completion()

    def fetch(self, session: McpSession, kind: str, *, quiet: bool = False) -> list[dict[str, Any]]:
        method, key, cap = LISTS[kind]
        try:
            items = session._paginated_list(method, key)
        except (McpError, TransportError, ConnectionError) as e:
            if not quiet:
                print(red(f"{method} failed: {e}"))
            items = []
        else:
            if cap not in session.server_capabilities and items:
                session.findings.violation(
                    f"{cap}-without-capability",
                    f"server answered {method} with {len(items)} item(s) but does not "
                    f"advertise the {cap!r} capability",
                    "basic/lifecycle#capability-negotiation",
                    level=Level.WARNING,
                )
        self.lists[kind] = items
        self.stale.discard(kind)
        return items

    def ensure(self, session: McpSession, kind: str) -> list[dict[str, Any]]:
        if kind in self.lists and kind not in self.stale:
            return self.lists[kind]
        cap = LISTS[kind][2]
        if kind not in self.lists and cap not in session.server_capabilities:
            print(dim(f"Server does not advertise {cap!r}; asking anyway."))
        items = self.fetch(session, kind)
        self.rebuild_completion()
        return items


def print_findings(findings: list[Finding]) -> None:
    for f in findings:
        colour = red if f.level >= Level.ERROR else yellow if f.level >= Level.WARNING else dim
        ref = f"  ({f.spec_ref})" if f.spec_ref else ""
        print(colour(f"  [{f.label()}] {f.message}{ref}"))


class Repl:
    """Interactive REPL for MCP tool invocation."""

    def __init__(self, session: McpSession, *, cache: SessionCache | None = None) -> None:
        self.session = session
        self.cache = cache if cache is not None else SessionCache.build(session)
        self.trace_enabled = session.transport.trace
        self.last_response: dict[str, Any] | None = None
        session.notification_listeners.append(self._on_notification)

    def _on_notification(self, msg: dict[str, Any]) -> None:
        kinds = _LIST_CHANGED.get(str(msg.get("method")))
        if kinds:
            self.cache.stale.update(kinds)

    def banner(self) -> None:
        server_name = sanitize(str(self.session.server_info.get("name", "MCP server")))
        print()
        print(bold(f"mcp-test-driver — connected to {server_name}"))
        rl_name, rl_version = readline_info()
        rl_line = f"REPL using {rl_name} version {rl_version}."
        if rl_name == "libedit":
            rl_line += "  Warning: tab-completion and F1/Esc-h unavailable."
        print(dim(rl_line))
        print(dim('Type "/list" to see tools, "/help" for usage, Ctrl-D to exit.'))
        print(dim("  Tool args: key=val  key=[a b]  key={k: v}  or raw JSON object."))
        tab_hint = "Tab-completion is available."
        if rl_name != "libedit":
            tab_hint += "  F1 or Esc-h for context help."
        print(dim(tab_hint))
        print()

    def run(self) -> None:
        self.banner()
        setup_readline(self.cache.completion)
        while True:
            self.before_prompt()
            try:
                line = input("mcp> ").lstrip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if line and not self.handle_line(line):
                break

    def before_prompt(self) -> None:
        """Show what arrived while idle, refresh stale lists, report findings."""
        try:
            self.session.pump()
        except TransportError as e:
            print(red(f"Transport error: {e}"))
        if self.cache.stale:
            for kind in sorted(self.cache.stale):
                if kind in self.cache.lists:
                    self.cache.fetch(self.session, kind)
            self.cache.stale.clear()
            self.cache.rebuild_completion()
            setup_readline(self.cache.completion)
            print(dim("(lists refreshed after list_changed)"))
        print_findings(self.session.findings.take_unshown())

    def handle_line(self, line: str) -> bool:
        """Run one input line.  Returns False if the REPL should exit."""
        parts = line.split(None, 1)
        word = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        try:
            if word.startswith(commands.BUILTIN_PREFIX):
                cmd = commands.resolve(word)
                if cmd is None:
                    print(red(f"Unknown command: {sanitize(word)}"))
                    print(dim('Type "/help" for usage.'))
                    return True
                return cmd.func(self, rest) is not False
            self.invoke_tool(word, rest)
        except RequestCancelled:
            pass
        except SessionExpiredError as e:
            print(red(f"{e}"))
        except TransportError as e:
            print(red(f"Transport error: {e}"))
        except ConnectionError as e:
            print(red(f"Connection lost: {e}"))
        except McpError as e:
            render_error({"code": e.code, "message": e.message, "data": e.data})
        except ValueError as e:
            print(red(f"Error: {e}"))
        except Exception as e:
            print(red(f"Unexpected error: {type(e).__name__}: {sanitize(str(e))}"))
        return True

    def show(self, resp: dict[str, Any]) -> None:
        self.last_response = resp
        render_response(resp)

    def refresh_readline(self) -> None:
        setup_readline(self.cache.completion)

    def invoke_tool(self, name: str, rest: str) -> None:
        state = self.cache.completion
        if name not in state.tool_names:
            print(red(f"Unknown tool '{sanitize(name)}'."), dim("(type '/list' to see tools)"))
            return
        try:
            arguments = parse_args(rest)
        except (json.JSONDecodeError, ValueError) as exc:
            print(red(f"Could not parse arguments: {exc}"))
            return
        headers = None
        if self.session.transport.kind == "http" and self.session.spec.has(Feature.METHOD_HEADERS):
            headers = param_headers(state.tool_schemas.get(name, {}), arguments) or None
        resp = self.session.call_tool(state.real_tool_name(name), arguments, headers=headers)
        self.show(resp)
