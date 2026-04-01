# Copyright 2026 Phil Pennock — see LICENSE file.

"""Interactive REPL for MCP tool invocation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .color import bold, dim, red
from .completion import (
    CompletionState,
    DOT_COMMAND_ALIASES,
    DOT_COMMANDS,
    setup_readline,
)
from .parse import parse_args
from .transport import TransportError, sanitize

if TYPE_CHECKING:
    from .protocol import McpSession


@dataclass
class SessionCache:
    """Cached data from the MCP server, discarded on reconnect."""

    tools: list[dict[str, Any]] = field(default_factory=list)
    server_info: dict[str, Any] = field(default_factory=dict)
    completion: CompletionState = field(default_factory=CompletionState)

    @classmethod
    def build(
        cls,
        tools: list[dict[str, Any]],
        server_info: dict[str, Any],
    ) -> SessionCache:
        return cls(
            tools=tools,
            server_info=server_info,
            completion=CompletionState.from_tools(tools),
        )


def _print_result(resp: dict[str, Any]) -> None:
    # Check for JSON-RPC error response
    error = resp.get("error")
    if isinstance(error, dict):
        code = error.get("code", "?")
        message = error.get("message", "Unknown error")
        print(red(f"Server error [{code}]: {message}"))
        data = error.get("data")
        if data:
            print(red(f"  {data}"))
        return

    result = resp.get("result")
    if not isinstance(result, dict):
        # No result and no error — unusual but not fatal
        if result is not None:
            print(str(result))
        return

    is_error = result.get("isError", False)
    content = result.get("content")
    if not isinstance(content, list):
        return

    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text", "")
        if not isinstance(text, str):
            text = str(text)
        if is_error:
            print(red(f"Error: {text}"))
        else:
            try:
                parsed = json.loads(text)
                print(json.dumps(parsed, indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                print(text)


class Repl:
    """Interactive REPL for MCP tool invocation."""

    def __init__(self, session: McpSession) -> None:
        self.session = session
        self.cache = SessionCache.build(
            session.list_tools(),
            session.server_info,
        )
        self._trace_enabled = True

    def run(self) -> None:
        server_name = sanitize(str(self.cache.server_info.get("name", "MCP server")))
        print()
        print(bold(f"mcp-test-driver — connected to {server_name}"))
        print(dim('Type ".list" to see tools, ".help" for usage, Ctrl-D to exit.'))
        print(dim("Tab-completion is available.  F1 or Esc-H for context help."))
        print()

        setup_readline(self.cache.completion)

        while True:
            try:
                line = input("mcp> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line:
                continue

            parts = line.split(None, 1)
            cmd = parts[0]
            rest = parts[1] if len(parts) > 1 else ""

            if cmd in DOT_COMMAND_ALIASES:
                cmd = DOT_COMMAND_ALIASES[cmd]

            try:
                if cmd.startswith("."):
                    if not self._dispatch_dot(cmd, rest):
                        break
                else:
                    self._invoke_tool(cmd, rest)
            except TransportError as e:
                print(red(f"Transport error: {e}"))
            except ConnectionError as e:
                print(red(f"Connection lost: {e}"))
            except Exception as e:
                print(red(f"Unexpected error: {type(e).__name__}: {e}"))

    def _dispatch_dot(self, cmd: str, rest: str) -> bool:
        """Handle a dot-command.  Returns False if the REPL should exit."""
        if cmd in (".quit",):
            return False
        if cmd == ".help":
            self._cmd_help(rest)
        elif cmd == ".list":
            self._cmd_list()
        elif cmd == ".describe":
            self._cmd_describe(rest)
        elif cmd == ".reconnect":
            self._cmd_reconnect()
        elif cmd == ".cache-flush":
            self._cmd_cache_flush()
        elif cmd == ".trace":
            self._cmd_trace()
        else:
            print(red(f"Unknown command: {cmd}"))
            print(dim('Type ".help" for usage.'))
        return True

    def _cmd_help(self, rest: str) -> None:
        rest = rest.strip()
        if rest:
            show_context_help_for(self.cache.completion, rest)
        else:
            _show_general_help()

    def _cmd_list(self) -> None:
        for t in self.cache.tools:
            name = sanitize(str(t.get("name", "?")))
            desc = sanitize(str(t.get("description", "")))
            print(f"  {bold(name)}: {dim(desc)}")

    def _cmd_describe(self, rest: str) -> None:
        name = rest.strip()
        if not name:
            print(red("Usage: .describe <tool>"))
            return
        from .completion import _show_tool_help

        _show_tool_help(self.cache.completion, name)

    def _cmd_reconnect(self) -> None:
        tools = self.session.reconnect()
        self.cache = SessionCache.build(tools, self.session.server_info)
        setup_readline(self.cache.completion)
        print(dim(f"Reconnected. {len(tools)} tools available."))

    def _cmd_cache_flush(self) -> None:
        tools = self.session.list_tools()
        self.cache = SessionCache.build(tools, self.session.server_info)
        setup_readline(self.cache.completion)
        print(dim(f"Cache flushed. {len(tools)} tools available."))

    def _cmd_trace(self) -> None:
        self._trace_enabled = not self._trace_enabled
        self.session.transport.trace = self._trace_enabled
        state = "on" if self._trace_enabled else "off"
        print(dim(f"Protocol tracing {state}."))

    def _invoke_tool(self, name: str, rest: str) -> None:
        if name not in self.cache.completion.tool_names:
            print(red(f"Unknown tool '{name}'."), dim("(type '.list' to see tools)"))
            return

        try:
            arguments = parse_args(rest)
        except (json.JSONDecodeError, ValueError) as exc:
            print(red(f"Could not parse arguments: {exc}"))
            return

        resp = self.session.call_tool(name, arguments)
        if resp is None:
            print(red("Server closed connection unexpectedly."))
            return

        _print_result(resp)


def show_context_help_for(state: CompletionState, text: str) -> None:
    """Show help for a partial input line (used by F1/Esc-H macro)."""
    parts = text.strip().split()
    if not parts:
        _show_general_help()
        return

    first = parts[0]
    if first in state.tool_names:
        if len(parts) > 1 and "=" in parts[-1]:
            key = parts[-1].partition("=")[0]
            desc_key = f"{first}:{key}"
            desc = state.arg_descriptions.get(desc_key)
            if desc:
                print(f"  {bold(key)}: {desc}")
                return
        from .completion import _show_tool_help

        _show_tool_help(state, first)
    else:
        print(red(f"Unknown: {first}"), dim("(type '.list' to see tools)"))


def _show_general_help() -> None:
    print()
    print("  Built-in commands (dot-prefixed):")
    for canonical, alias, desc in DOT_COMMANDS:
        print(f"    {bold(canonical):24s} {alias:6s}  {dim(desc)}")
    print()
    print("  Tool invocation:")
    print(f"    {bold('<tool>'):<24s}         call with no arguments")
    print(f"    {bold('<tool> key=val ...'):<24s}         call with keyword arguments")
    print(
        f"    {bold('<tool> ' + chr(123) + '...' + chr(125)):<24s}         call with raw JSON object"
    )
    print()
    print("  Key=value notes:")
    print("    Booleans: exact=true  exact=false")
    print("    Integers: decimal=10003  (parsed automatically)")
    print(
        "    Strings:  all other values; quote with shell rules if they contain spaces"
    )
    print()
    print(f"  Press {bold('Tab')} to complete, {bold('F1')}/{bold('Esc-H')} for help.")
    print()
