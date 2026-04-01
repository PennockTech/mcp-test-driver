# Copyright 2026 Phil Pennock — see LICENSE file.

"""Interactive REPL for MCP tool invocation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .color import bold, dim, red
from .completion import (
    BUILTIN_ALIASES,
    BUILTIN_COMMANDS,
    BUILTIN_PREFIX,
    CompletionState,
    readline_info,
    schedule_restore_input,
    setup_readline,
)
from .parse import parse_args
from .protocol import McpError
from .transport import TransportError, sanitize

if TYPE_CHECKING:
    from .protocol import McpSession


@dataclass
class SessionCache:
    """Cached data from the MCP server, discarded on reconnect."""

    tools: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    resource_templates: list[dict[str, Any]] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    server_info: dict[str, Any] = field(default_factory=dict)
    completion: CompletionState = field(default_factory=CompletionState)

    @classmethod
    def build(cls, session: McpSession) -> SessionCache:
        tools = session.list_tools()

        # Fetch resources, templates, and prompts if the server supports them.
        # Servers that don't support these will return errors — catch and skip.
        resources: list[dict[str, Any]] = []
        resource_templates: list[dict[str, Any]] = []
        prompts: list[dict[str, Any]] = []

        caps = session.server_capabilities
        if "resources" in caps:
            try:
                resources = session.list_resources()
            except (McpError, TransportError, ConnectionError):
                pass
            try:
                resource_templates = session.list_resource_templates()
            except (McpError, TransportError, ConnectionError):
                pass
        if "prompts" in caps:
            try:
                prompts = session.list_prompts()
            except (McpError, TransportError, ConnectionError):
                pass

        state = CompletionState.from_tools(tools)
        state.resource_uris = [
            sanitize(str(r.get("uri", ""))) for r in resources if r.get("uri")
        ]
        state.prompt_names = [
            sanitize(str(p.get("name", ""))) for p in prompts if p.get("name")
        ]

        return cls(
            tools=tools,
            resources=resources,
            resource_templates=resource_templates,
            prompts=prompts,
            server_info=session.server_info,
            completion=state,
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
        # For responses without content (e.g., prompt results, resource reads),
        # pretty-print the entire result.
        print(json.dumps(result, indent=2, ensure_ascii=False))
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
        self.cache = SessionCache.build(session)
        self._trace_enabled = session.transport.trace

    def run(self) -> None:
        server_name = sanitize(str(self.cache.server_info.get("name", "MCP server")))
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

        setup_readline(self.cache.completion)

        while True:
            try:
                line = input("mcp> ").lstrip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line:
                continue

            parts = line.split(None, 1)
            cmd = parts[0]
            rest = parts[1] if len(parts) > 1 else ""

            if cmd in BUILTIN_ALIASES:
                cmd = BUILTIN_ALIASES[cmd]

            try:
                if cmd.startswith(BUILTIN_PREFIX):
                    if not self._dispatch_builtin(cmd, rest):
                        break
                else:
                    self._invoke_tool(cmd, rest)
            except TransportError as e:
                print(red(f"Transport error: {e}"))
            except ConnectionError as e:
                print(red(f"Connection lost: {e}"))
            except McpError as e:
                print(red(f"MCP error [{e.code}]: {e.message}"))
                if e.data:
                    print(red(f"  {e.data}"))
            except Exception as e:
                print(red(f"Unexpected error: {type(e).__name__}: {e}"))

    def _dispatch_builtin(self, cmd: str, rest: str) -> bool:
        """Handle a builtin command.  Returns False if the REPL should exit."""
        if cmd in ("/quit",):
            return False
        if cmd == "/help":
            self._cmd_help(rest)
        elif cmd == "/list":
            self._cmd_list()
        elif cmd == "/describe":
            self._cmd_describe(rest)
        elif cmd == "/resources":
            self._cmd_resources()
        elif cmd == "/templates":
            self._cmd_templates()
        elif cmd == "/read":
            self._cmd_read(rest)
        elif cmd == "/prompts":
            self._cmd_prompts()
        elif cmd == "/prompt":
            self._cmd_prompt(rest)
        elif cmd == "/ping":
            self._cmd_ping()
        elif cmd == "/loglevel":
            self._cmd_loglevel(rest)
        elif cmd == "/roots":
            self._cmd_roots(rest)
        elif cmd == "/subscribe":
            self._cmd_subscribe(rest)
        elif cmd == "/unsubscribe":
            self._cmd_unsubscribe(rest)
        elif cmd == "/reconnect":
            self._cmd_reconnect()
        elif cmd == "/cache-flush":
            self._cmd_cache_flush()
        elif cmd == "/trace":
            self._cmd_trace()
        else:
            print(red(f"Unknown command: {cmd}"))
            print(dim('Type "/help" for usage.'))
        return True

    def _cmd_help(self, rest: str) -> None:
        original = rest
        rest = rest.strip()
        if rest:
            show_context_help_for(self.cache.completion, rest)
            schedule_restore_input(original)
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
            print(red("Usage: /describe <tool>"))
            return
        from .completion import _show_tool_help

        _show_tool_help(self.cache.completion, name)

    def _cmd_resources(self) -> None:
        if not self.cache.resources:
            print(dim("No resources available."))
            return
        for r in self.cache.resources:
            uri = sanitize(str(r.get("uri", "?")))
            name = sanitize(str(r.get("name", "")))
            mime = sanitize(str(r.get("mimeType", "")))
            desc = sanitize(str(r.get("description", "")))
            line = f"  {bold(uri)}"
            if name:
                line += f"  {name}"
            if mime:
                line += f"  {dim('[' + mime + ']')}"
            print(line)
            if desc:
                print(f"    {dim(desc)}")

    def _cmd_templates(self) -> None:
        if not self.cache.resource_templates:
            print(dim("No resource templates available."))
            return
        for t in self.cache.resource_templates:
            uri = sanitize(str(t.get("uriTemplate", "?")))
            name = sanitize(str(t.get("name", "")))
            mime = sanitize(str(t.get("mimeType", "")))
            desc = sanitize(str(t.get("description", "")))
            line = f"  {bold(uri)}"
            if name:
                line += f"  {name}"
            if mime:
                line += f"  {dim('[' + mime + ']')}"
            print(line)
            if desc:
                print(f"    {dim(desc)}")

    def _cmd_read(self, rest: str) -> None:
        uri = rest.strip()
        if not uri:
            print(red("Usage: /read <uri>"))
            return
        resp = self.session.read_resource(uri)
        if resp is None:
            print(red("Server closed connection."))
            return
        _print_result(resp)

    def _cmd_prompts(self) -> None:
        if not self.cache.prompts:
            print(dim("No prompts available."))
            return
        for p in self.cache.prompts:
            name = sanitize(str(p.get("name", "?")))
            desc = sanitize(str(p.get("description", "")))
            print(f"  {bold(name)}: {dim(desc)}")
            args = p.get("arguments", [])
            if isinstance(args, list):
                for arg in args:
                    if not isinstance(arg, dict):
                        continue
                    aname = sanitize(str(arg.get("name", "?")))
                    adesc = sanitize(str(arg.get("description", "")))
                    req = " (required)" if arg.get("required") else ""
                    print(f"    {bold(aname + '=')}{req}  {dim(adesc)}")

    def _cmd_prompt(self, rest: str) -> None:
        parts = rest.strip().split(None, 1)
        if not parts:
            print(red("Usage: /prompt <name> [key=val ...]"))
            return
        name = parts[0]
        arg_text = parts[1] if len(parts) > 1 else ""
        try:
            arguments = parse_args(arg_text) if arg_text else None
        except (json.JSONDecodeError, ValueError) as exc:
            print(red(f"Could not parse arguments: {exc}"))
            return
        resp = self.session.get_prompt(name, arguments)
        if resp is None:
            print(red("Server closed connection."))
            return
        _print_result(resp)

    def _cmd_ping(self) -> None:
        self.session.ping()
        print(dim("Pong."))

    def _cmd_loglevel(self, rest: str) -> None:
        level = rest.strip()
        if not level:
            print(red("Usage: /loglevel <level>"))
            print(
                dim(
                    "  Levels: debug, info, notice, warning, error, critical, alert, emergency"
                )
            )
            return
        self.session.set_log_level(level)
        print(dim(f"Log level set to {level}."))

    def _cmd_subscribe(self, rest: str) -> None:
        uri = rest.strip()
        if not uri:
            print(red("Usage: /subscribe <uri>"))
            return
        self.session.subscribe_resource(uri)
        print(dim(f"Subscribed to {uri}."))

    def _cmd_unsubscribe(self, rest: str) -> None:
        uri = rest.strip()
        if not uri:
            print(red("Usage: /unsubscribe <uri>"))
            return
        self.session.unsubscribe_resource(uri)
        print(dim(f"Unsubscribed from {uri}."))

    def _cmd_roots(self, rest: str) -> None:
        """Handle /roots [on [path] | off] — show or toggle the roots capability."""
        from pathlib import Path

        rest = rest.strip()
        if not rest:
            # Show current state
            handler = self.session.roots_handler
            if handler is None:
                print(dim("Roots: disabled"))
                print(dim("  Use /roots on [path] to enable, then /reconnect."))
            else:
                print(bold("Roots: enabled"))
                print(f"  Base: {handler.base}")
                for r in handler.roots:
                    print(f"  {dim(r['uri'])}  {r.get('name', '')}")
            return

        parts = rest.split(None, 1)
        subcmd = parts[0].lower()

        if subcmd == "on":
            path = Path(parts[1]) if len(parts) > 1 else Path.cwd()
            try:
                self.session.enable_roots(path)
            except (OSError, ValueError) as e:
                print(red(f"Cannot set roots: {e}"))
                return
            handler = self.session.roots_handler
            if handler:
                print(dim(f"Roots enabled for {handler.base}"))
            print(dim("Run /reconnect for the server to see this."))
        elif subcmd == "off":
            self.session.disable_roots()
            print(dim("Roots disabled."))
            print(dim("Run /reconnect for the server to see this."))
        else:
            print(red("Usage: /roots [on [path] | off]"))

    def _cmd_reconnect(self) -> None:
        tools = self.session.reconnect()
        self.cache = SessionCache.build(self.session)
        setup_readline(self.cache.completion)
        print(dim(f"Reconnected. {len(tools)} tools available."))

    def _cmd_cache_flush(self) -> None:
        self.cache = SessionCache.build(self.session)
        setup_readline(self.cache.completion)
        print(dim(f"Cache flushed. {len(self.cache.tools)} tools available."))

    def _cmd_trace(self) -> None:
        self._trace_enabled = not self._trace_enabled
        self.session.transport.trace = self._trace_enabled
        state = "on" if self._trace_enabled else "off"
        print(dim(f"Protocol tracing {state}."))

    def _invoke_tool(self, name: str, rest: str) -> None:
        if name not in self.cache.completion.tool_names:
            print(red(f"Unknown tool '{name}'."), dim("(type '/list' to see tools)"))
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
    """Show help for a partial input line (used by F1/Esc-h macro)."""
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
        print(red(f"Unknown: {first}"), dim("(type '/list' to see tools)"))


def _show_general_help() -> None:
    print()
    print("  Built-in commands (/-prefixed):")
    for canonical, alias, desc in BUILTIN_COMMANDS:
        alias_col = alias if alias else "    "
        print(f"    {bold(canonical):24s} {alias_col:6s}  {dim(desc)}")
    print()
    print("  Tool invocation:")
    print(f"    {bold('<tool>'):<24s}         call with no arguments")
    print(f"    {bold('<tool> key=val ...'):<24s}         call with keyword arguments")
    print(f"    {bold('<tool> key=[a b] ...'):<24s}         list value")
    print(f"    {bold('<tool> key=' + chr(123) + 'k: v' + chr(125)):<24s}         dict value")
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
    print("    Lists:    paths=[src/foo.py src/bar.py]  tags=[\"hello world\", foo, 42]")
    print("    Dicts:    filter={type: file, recursive: true}")
    print()
    print(f"  Press {bold('Tab')} to complete, {bold('F1')}/{bold('Esc-h')} for help.")
    print()
