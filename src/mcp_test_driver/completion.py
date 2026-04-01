# Copyright 2026 Phil Pennock — see LICENSE file.

"""Readline tab-completion and help keybindings for the MCP REPL."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .color import bold, dim, red
from .transport import sanitize

# Builtin command prefix.  Tool names are stripped of this prefix during
# sanitization so that a malicious server cannot shadow builtins.
BUILTIN_PREFIX = "/"

# Builtin command definitions: (canonical, alias, description)
BUILTIN_COMMANDS: list[tuple[str, str, str]] = [
    ("/help", "/h", "Show help (or /help <tool> for tool help)"),
    ("/list", "/l", "List available tools"),
    ("/describe", "/d", "Show full schema for a tool"),
    ("/resources", "/lr", "List available resources"),
    ("/templates", "/lt", "List resource templates"),
    ("/read", "/r", "Read a resource by URI"),
    ("/prompts", "/lp", "List available prompts"),
    ("/prompt", "/p", "Get a prompt (with optional arguments)"),
    ("/ping", "", "Ping the server"),
    ("/loglevel", "/ll", "Set server log level"),
    ("/roots", "", "Show/toggle roots capability (on [path] | off)"),
    ("/subscribe", "/sub", "Subscribe to resource updates"),
    ("/unsubscribe", "/unsub", "Unsubscribe from resource updates"),
    ("/reconnect", "/rc", "Reconnect to the server"),
    ("/cache-flush", "/cf", "Clear cached tools, re-fetch from server"),
    ("/trace", "/t", "Toggle JSON-RPC protocol tracing"),
    ("/quit", "/q", "Exit"),
]

BUILTIN_NAMES: set[str] = set()
BUILTIN_ALIASES: dict[str, str] = {}  # alias → canonical
for _canonical, _alias, _desc in BUILTIN_COMMANDS:
    BUILTIN_NAMES.add(_canonical)
    if _alias:
        BUILTIN_ALIASES[_alias] = _canonical


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
    # Resources and prompts for tab-completion of /read, /prompt, etc.
    resource_uris: list[str] = field(default_factory=list)
    prompt_names: list[str] = field(default_factory=list)

    @classmethod
    def from_tools(cls, tools: list[dict[str, Any]]) -> CompletionState:
        state = cls()
        for t in tools:
            # Sanitize all server-supplied data to prevent terminal injection
            # and readline confusion.  Tool names must be safe for readline.
            raw_name = str(t.get("name", ""))
            name = sanitize(raw_name).replace(" ", "_")
            # Strip the builtin prefix so server tools cannot shadow builtins.
            name = name.lstrip(BUILTIN_PREFIX)
            if not name:
                continue
            state.tool_names.add(name)
            state.tool_descriptions[name] = sanitize(str(t.get("description", "")))
            schema = t.get("inputSchema", {})
            state.tool_schemas[name] = schema
            props = schema.get("properties", {})
            keys = sorted(sanitize(str(k)) for k in props.keys())
            state.tool_args[name] = [k + "=" for k in keys if k]
            for k, v in props.items():
                k = sanitize(str(k))
                if not k:
                    continue
                if "enum" in v:
                    state.arg_enums[f"{name}:{k}"] = [
                        sanitize(str(e)) for e in v["enum"]
                    ]
                desc_parts: list[str] = []
                if "type" in v:
                    desc_parts.append(f"type: {sanitize(str(v['type']))}")
                if "description" in v:
                    desc_parts.append(sanitize(str(v["description"])))
                if "enum" in v:
                    desc_parts.append(f"enum: {v['enum']}")
                state.arg_descriptions[f"{name}:{k}"] = " | ".join(desc_parts)
        state.all_first_words = (
            BUILTIN_NAMES | set(BUILTIN_ALIASES.keys()) | state.tool_names
        )
        return state


def make_completer(state: CompletionState):  # noqa: ANN201
    """Return a readline completer function with closure over completion state."""

    def completer(text: str, idx: int) -> str | None:
        rl = _get_readline()
        if rl is None:
            return None
        buf = rl.get_line_buffer()
        begin = rl.get_begidx()

        if begin == 0:
            matches = sorted(c for c in state.all_first_words if c.startswith(text))
        else:
            first_word = buf[:begin].split()[0] if buf[:begin].strip() else ""
            # Resolve aliases
            if first_word in BUILTIN_ALIASES:
                first_word = BUILTIN_ALIASES[first_word]
            # /describe, /help take a tool name as argument
            if first_word in ("/describe", "/help"):
                matches = sorted(n for n in state.tool_names if n.startswith(text))
            elif first_word == "/prompt":
                matches = sorted(n for n in state.prompt_names if n.startswith(text))
            elif first_word in ("/read", "/subscribe", "/unsubscribe"):
                matches = sorted(u for u in state.resource_uris if u.startswith(text))
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
    rl = _get_readline()
    if rl is None:
        return
    buf = rl.get_line_buffer().strip()

    if not buf:
        _show_general_help()
        return

    parts = buf.split()
    first = parts[0]

    # Resolve alias
    if first in BUILTIN_ALIASES:
        first = BUILTIN_ALIASES[first]

    if first in BUILTIN_NAMES:
        # Help for a builtin; if /help or /describe with a tool arg, show tool help
        if first in ("/help", "/describe") and len(parts) > 1:
            _show_tool_help(state, parts[1])
        else:
            for canonical, alias, desc in BUILTIN_COMMANDS:
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
    for canonical, alias, desc in BUILTIN_COMMANDS:
        print(f"    {bold(canonical):24s} {alias:6s}  {dim(desc)}")
    print(f"\n  Or type a tool name — use {bold('/list')} to see available tools.")
    print(
        f"  Press {bold('Tab')} to complete, {bold('F1')}/{bold('Esc-h')} for help.\n"
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
            k = sanitize(str(k))
            req = " (required)" if k in required else ""
            typ = sanitize(str(v.get("type", "")))
            d = sanitize(str(v.get("description", "")))
            enum = v.get("enum")
            line = f"    {bold(k + '='):<24s} {dim(str(typ))}{req}"
            if d:
                line += f"  {d}"
            print(line)
            if enum:
                print(f"      values: {', '.join(sanitize(str(e)) for e in enum)}")
    print()


def _get_readline() -> object | None:
    """Return the best available readline module, or None.

    Preference order: gnureadline (always GNU) → readline (may be libedit).
    """
    try:
        import gnureadline
        return gnureadline
    except ImportError:
        pass
    try:
        import readline
        return readline
    except ImportError:
        return None


def _readline_is_libedit(rl: object) -> bool:
    """Return True if the readline module is backed by libedit/editline."""
    backend = getattr(rl, "backend", None)
    if backend is not None:
        return backend == "editline"
    doc = getattr(rl, "__doc__", "") or ""
    lib_ver = getattr(rl, "_READLINE_LIBRARY_VERSION", "") or ""
    return "libedit" in doc or "EditLine" in lib_ver


def readline_info() -> tuple[str, str]:
    """Return (name, version) for the active readline library.

    Name is 'libedit' or 'readline'.  Version is a 'major.minor' string, or
    'unknown' if no version information is available.
    """
    rl = _get_readline()
    if rl is None:
        return ("readline", "unknown")

    lib_ver = getattr(rl, "_READLINE_LIBRARY_VERSION", "") or ""
    ver_int = getattr(rl, "_READLINE_VERSION", None)

    is_libedit = _readline_is_libedit(rl)
    name = "libedit" if is_libedit else "readline"

    # _READLINE_LIBRARY_VERSION is e.g. "8.2" for GNU readline but
    # "EditLine wrapper" for libedit, so fall back to decoding the integer.
    import re

    if re.match(r"^\d+\.\d+", lib_ver):
        version = lib_ver
    elif ver_int is not None:
        version = f"{ver_int >> 8}.{ver_int & 0xFF}"
    else:
        version = "unknown"

    return (name, version)


def schedule_restore_input(text: str) -> None:
    """Pre-fill the readline buffer with text on the next prompt.

    Called after the F1/Esc-h help macro to restore the line that was
    submitted as '/help <text>'.
    """
    rl = _get_readline()
    if rl is None:
        return
    set_hook = getattr(rl, "set_pre_input_hook", None)
    if set_hook is None:
        return

    def _hook() -> None:
        rl.set_pre_input_hook(None)  # type: ignore[union-attr]
        rl.insert_text(text)  # type: ignore[union-attr]
        rl.redisplay()  # type: ignore[union-attr]

    set_hook(_hook)


def setup_readline(state: CompletionState) -> bool:
    """Configure readline with completion and help keybindings.

    Returns True if readline was successfully configured.
    """
    import sys

    rl = _get_readline()
    if rl is None:
        return False

    rl.set_completer(make_completer(state))
    rl.set_completer_delims(" ")

    is_libedit = _readline_is_libedit(rl)

    # Tab completion binding syntax differs between GNU readline and libedit.
    if is_libedit:
        rl.parse_and_bind("bind ^I rl_complete")
    else:
        rl.parse_and_bind("tab: complete")

    # F1 and Esc-h: context-sensitive help.
    # Macro: go-to-start, insert "/help ", submit.  Prepending rather than
    # kill/yank avoids kill-ring contamination: \C-k on an empty line does
    # not update the kill ring, so a subsequent \C-y would yank stale content
    # from a previous F1 press forever.  With prepend, an empty buffer yields
    # "/help " with empty rest, which correctly falls through to general help.
    # Only bind when stdin is a TTY — macro bindings emit errors to stderr
    # when there is no terminal.
    # libedit macro bindings corrupt key state (binding \eH/\eOP makes the
    # letter 'e' unenterable), so skip them on libedit entirely.
    if sys.stdin.isatty() and not is_libedit:
        macro = r'"\C-a/help \C-m"'
        rl.parse_and_bind(r'"\eh": ' + macro)     # Esc-h
        rl.parse_and_bind(r'"\eOP": ' + macro)    # F1 (xterm/VT220)
        rl.parse_and_bind(r'"\e[11~": ' + macro)  # F1 alternate

    return True
