# Copyright 2026 Phil Pennock — see LICENSE file.

"""Readline tab-completion and help keybindings for the MCP REPL."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .color import bold, dim, red


# Dot-command definitions: (canonical, alias, description)
DOT_COMMANDS: list[tuple[str, str, str]] = [
    (".help", ".h", "Show help (or .help <tool> for tool help)"),
    (".list", ".l", "List available tools"),
    (".describe", ".d", "Show full schema for a tool"),
    (".reconnect", ".rc", "Reconnect to the server"),
    (".cache-flush", ".cf", "Clear cached tools, re-fetch from server"),
    (".trace", ".t", "Toggle JSON-RPC protocol tracing"),
    (".quit", ".q", "Exit"),
]

DOT_COMMAND_NAMES: set[str] = set()
DOT_COMMAND_ALIASES: dict[str, str] = {}  # alias → canonical
for _canonical, _alias, _desc in DOT_COMMANDS:
    DOT_COMMAND_NAMES.add(_canonical)
    DOT_COMMAND_ALIASES[_alias] = _canonical


@dataclass
class CompletionState:
    """Holds derived completion data from the tools list."""

    tool_names: set[str] = field(default_factory=set)
    tool_descriptions: dict[str, str] = field(default_factory=dict)
    tool_schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_args: dict[str, list[str]] = field(default_factory=dict)
    arg_enums: dict[str, list[str]] = field(default_factory=dict)
    arg_descriptions: dict[str, str] = field(default_factory=dict)
    all_first_words: set[str] = field(default_factory=set)

    @classmethod
    def from_tools(cls, tools: list[dict[str, Any]]) -> CompletionState:
        state = cls()
        for t in tools:
            name = str(t["name"])
            state.tool_names.add(name)
            state.tool_descriptions[name] = str(t.get("description", ""))
            schema = t.get("inputSchema", {})
            state.tool_schemas[name] = schema
            props = schema.get("properties", {})
            keys = sorted(props.keys())
            state.tool_args[name] = [k + "=" for k in keys]
            for k, v in props.items():
                if "enum" in v:
                    state.arg_enums[f"{name}:{k}"] = [str(e) for e in v["enum"]]
                desc_parts: list[str] = []
                if "type" in v:
                    desc_parts.append(f"type: {v['type']}")
                if "description" in v:
                    desc_parts.append(str(v["description"]))
                if "enum" in v:
                    desc_parts.append(f"enum: {v['enum']}")
                state.arg_descriptions[f"{name}:{k}"] = " | ".join(desc_parts)
        state.all_first_words = (
            DOT_COMMAND_NAMES | set(DOT_COMMAND_ALIASES.keys()) | state.tool_names
        )
        return state


def make_completer(state: CompletionState):  # noqa: ANN201
    """Return a readline completer function with closure over completion state."""

    def completer(text: str, idx: int) -> str | None:
        import readline

        buf = readline.get_line_buffer()
        begin = readline.get_begidx()

        if begin == 0:
            matches = sorted(c for c in state.all_first_words if c.startswith(text))
        else:
            first_word = buf[:begin].split()[0] if buf[:begin].strip() else ""
            # Resolve aliases
            if first_word in DOT_COMMAND_ALIASES:
                first_word = DOT_COMMAND_ALIASES[first_word]
            # .describe and .help take a tool name as argument
            if first_word in (".describe", ".help"):
                matches = sorted(n for n in state.tool_names if n.startswith(text))
            elif first_word in state.tool_names:
                if "=" in text:
                    key, _, partial = text.partition("=")
                    enum_key = f"{first_word}:{key}"
                    if enum_key in state.arg_enums:
                        prefix = key + "="
                        matches = sorted(
                            prefix + v
                            for v in state.arg_enums[enum_key]
                            if v.startswith(partial)
                        )
                    else:
                        matches = []
                else:
                    matches = sorted(
                        k
                        for k in state.tool_args.get(first_word, [])
                        if k.startswith(text)
                    )
            else:
                matches = []

        return matches[idx] if idx < len(matches) else None

    return completer


def show_context_help(state: CompletionState) -> None:
    """Display context-sensitive help based on current readline buffer."""
    try:
        import readline

        buf = readline.get_line_buffer().strip()
    except ImportError:
        return

    if not buf:
        _show_general_help()
        return

    parts = buf.split()
    first = parts[0]

    # Resolve alias
    if first in DOT_COMMAND_ALIASES:
        first = DOT_COMMAND_ALIASES[first]

    if first in DOT_COMMAND_NAMES:
        # Help for a dot-command; if .help/.describe with a tool arg, show tool help
        if first in (".help", ".describe") and len(parts) > 1:
            _show_tool_help(state, parts[1])
        else:
            for canonical, alias, desc in DOT_COMMANDS:
                if canonical == first:
                    print(f"\n  {bold(canonical)} ({alias}): {desc}")
                    break
        return

    if first in state.tool_names:
        # If we're mid-argument (after key=), show that argument's info
        if len(parts) > 1 and "=" in parts[-1]:
            key = parts[-1].partition("=")[0]
            desc_key = f"{first}:{key}"
            if desc_key in state.arg_descriptions:
                print(f"\n  {bold(key)}: {state.arg_descriptions[desc_key]}")
                return
        _show_tool_help(state, first)
        return

    print(f"\n  {red('Unknown:')} {first}")


def _show_general_help() -> None:
    print("\n  Built-in commands:")
    for canonical, alias, desc in DOT_COMMANDS:
        print(f"    {bold(canonical):24s} {alias:6s}  {dim(desc)}")
    print(f"\n  Or type a tool name — use {bold('.list')} to see available tools.")
    print(
        f"  Press {bold('Tab')} to complete, {bold('F1')}/{bold('Esc-H')} for help.\n"
    )


def _show_tool_help(state: CompletionState, name: str) -> None:
    if name not in state.tool_names:
        print(f"\n  {red('Unknown tool:')} {name}")
        return
    desc = state.tool_descriptions.get(name, "")
    print(f"\n  {bold(name)}: {desc}")
    schema = state.tool_schemas.get(name, {})
    props = schema.get("properties", {})
    required = schema.get("required", [])
    if props:
        print("  Arguments:")
        for k, v in sorted(props.items()):
            req = " (required)" if k in required else ""
            typ = v.get("type", "")
            d = v.get("description", "")
            enum = v.get("enum")
            line = f"    {bold(k + '='):<24s} {dim(str(typ))}{req}"
            if d:
                line += f"  {d}"
            print(line)
            if enum:
                print(f"      values: {', '.join(str(e) for e in enum)}")
    print()


def setup_readline(state: CompletionState) -> bool:
    """Configure readline with completion and help keybindings.

    Returns True if readline was successfully configured.
    """
    try:
        import readline
    except ImportError:
        return False

    readline.set_completer(make_completer(state))
    readline.set_completer_delims(" ")
    readline.parse_and_bind("tab: complete")

    # F1 and Esc-H: context-sensitive help.
    # Bind to a readline macro that prepends ".help " to the current line
    # and submits it.  The REPL intercepts .help and shows context help,
    # then the user gets a fresh prompt.
    # Macro: go-to-start, kill-to-end, type ".help ", yank-back, accept.
    # Only bind when stdin is a TTY — readline macro bindings emit errors
    # to stderr when there is no terminal.
    import sys

    if sys.stdin.isatty():
        macro = r'"\C-a\C-k.help \C-y\C-m"'
        doc = getattr(readline, "__doc__", "") or ""
        if "libedit" in doc:
            readline.parse_and_bind("bind \\eH " + macro)
            readline.parse_and_bind("bind \\eOP " + macro)
        else:
            readline.parse_and_bind(r'"\eH": ' + macro)
            readline.parse_and_bind(r'"\eOP": ' + macro)
            readline.parse_and_bind(r'"\e[11~": ' + macro)  # F1 alternate

    return True
