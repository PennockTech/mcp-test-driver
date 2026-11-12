# Copyright 2026 Phil Pennock — see LICENSE file.

"""Readline tab-completion and help keybindings for the MCP REPL."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from . import commands, versions
from .sanitize import sanitize


class ReadlineModule(Protocol):
    """Structural type for the readline module (GNU readline or libedit).

    Captures the subset of the stdlib `readline` C-extension API that this
    module actually uses.  Both GNU readline (via the stdlib `readline` or
    the third-party `gnureadline`) and BSD libedit (via stdlib `readline`
    on macOS without `gnureadline`) expose these methods at runtime,
    although there are divergences between them:

    - Tab-completion bind syntax differs (handled at the call site).
    - libedit historically lacks `set_pre_input_hook`; callers using it
      should still defensively check via `getattr` before calling.
    - Probe attributes such as ``backend``, ``__doc__``, and
      ``_READLINE_LIBRARY_VERSION`` vary between implementations and are
      not part of this protocol; access them via :func:`getattr`.
    """

    def get_line_buffer(self) -> str: ...
    def get_begidx(self) -> int: ...
    def set_completer(
        self,
        function: Callable[[str, int], str | None] | None = ...,
        /,
    ) -> None: ...
    def set_completer_delims(self, string: str, /) -> None: ...
    def parse_and_bind(self, string: str, /) -> None: ...
    def insert_text(self, string: str, /) -> None: ...
    def redisplay(self) -> None: ...
    def set_pre_input_hook(
        self,
        function: Callable[[], object] | None = ...,
        /,
    ) -> None: ...


@dataclass
class CompletionState:
    """Completion and help data derived from the server's tools, prompts and resources.

    Every server-supplied string is sanitized here, once.  Tool names are
    also made readline-safe (no spaces) and stripped of the builtin prefix,
    so `display → real` maps what the user types to what the server expects.
    """

    tool_names: set[str] = field(default_factory=set)
    tool_real_names: dict[str, str] = field(default_factory=dict)
    # Raw (unsanitized) definitions, for /describe; sanitize when printing.
    tool_defs: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_descriptions: dict[str, str] = field(default_factory=dict)
    tool_schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_args: dict[str, list[str]] = field(default_factory=dict)
    arg_enums: dict[str, list[str]] = field(default_factory=dict)
    arg_descriptions: dict[str, str] = field(default_factory=dict)
    all_first_words: set[str] = field(default_factory=set)
    resource_uris: list[str] = field(default_factory=list)
    prompt_names: list[str] = field(default_factory=list)
    prompt_args: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_tools(
        cls,
        tools: list[dict[str, Any]],
        *,
        prompts: list[dict[str, Any]] | None = None,
        resources: list[dict[str, Any]] | None = None,
    ) -> CompletionState:
        state = cls()
        for t in tools:
            raw_name = t.get("name", "")
            if not isinstance(raw_name, str):
                continue
            name = commands.display_tool_name(raw_name)
            if not name:
                continue
            state.tool_names.add(name)
            state.tool_real_names[name] = raw_name
            state.tool_defs[name] = t
            state.tool_descriptions[name] = sanitize(str(t.get("description", "")))
            schema = t.get("inputSchema", {})
            if not isinstance(schema, dict):
                schema = {}
            state.tool_schemas[name] = schema
            props = schema.get("properties", {})
            if not isinstance(props, dict):
                props = {}
            keys = sorted(sanitize(str(k)) for k in props)
            state.tool_args[name] = [k + "=" for k in keys if k]
            for k, v in props.items():
                k = sanitize(str(k))
                if not k or not isinstance(v, dict):
                    continue
                enum = _enum_values(v)
                if enum:
                    state.arg_enums[f"{name}:{k}"] = enum
                desc_parts: list[str] = []
                if "type" in v:
                    desc_parts.append(f"type: {sanitize(str(v['type']))}")
                if "description" in v:
                    desc_parts.append(sanitize(str(v["description"])))
                if enum:
                    desc_parts.append(f"enum: {', '.join(enum)}")
                state.arg_descriptions[f"{name}:{k}"] = " | ".join(desc_parts)
        for p in prompts or []:
            pname = p.get("name")
            if not isinstance(pname, str) or not sanitize(pname):
                continue
            pname = sanitize(pname)
            state.prompt_names.append(pname)
            args = p.get("arguments")
            if isinstance(args, list):
                state.prompt_args[pname] = [
                    sanitize(str(a.get("name"))) + "="
                    for a in args
                    if isinstance(a, dict) and a.get("name")
                ]
        state.resource_uris = [
            sanitize(str(r.get("uri"))) for r in resources or [] if isinstance(r, dict) and r.get("uri")
        ]
        state.all_first_words = commands.all_words() | state.tool_names
        return state

    def real_tool_name(self, display: str) -> str:
        return self.tool_real_names.get(display, display)


def _enum_values(prop: dict[str, Any]) -> list[str]:
    """Enum choices from a property schema, including titled oneOf/anyOf forms."""
    if isinstance(prop.get("enum"), list):
        return [sanitize(str(e)) for e in prop["enum"]]
    for key in ("oneOf", "anyOf"):
        alts = prop.get(key)
        if isinstance(alts, list) and alts and all(isinstance(a, dict) and "const" in a for a in alts):
            return [sanitize(str(a["const"])) for a in alts]
    items = prop.get("items")
    if prop.get("type") == "array" and isinstance(items, dict):
        return _enum_values(items)
    return []


_LOG_LEVELS = ["debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"]


def _arg_matches(cmd: commands.Command, state: CompletionState, text: str) -> list[str]:
    match cmd.arg:
        case commands.Arg.TOOL:
            pool: Collection[str] = state.tool_names
        case commands.Arg.PROMPT:
            pool = state.prompt_names
        case commands.Arg.RESOURCE:
            pool = state.resource_uris
        case commands.Arg.COMMAND:
            pool = state.all_first_words
        case commands.Arg.LEVEL:
            pool = _LOG_LEVELS
        case commands.Arg.METHOD:
            pool = _ALL_CLIENT_METHODS
        case _:
            pool = []
    return sorted({*pool, *cmd.words} if cmd.words else set(pool))


_ALL_CLIENT_METHODS = frozenset().union(
    *(s.client_requests | s.client_notifications for s in versions.SPECS.values())
)


def make_completer(state: CompletionState):  # noqa: ANN201
    """Return a readline completer function with closure over completion state."""

    def completer(text: str, idx: int) -> str | None:
        rl = _get_readline()
        if rl is None:
            return None
        buf = rl.get_line_buffer()
        begin = rl.get_begidx()
        before = buf[:begin].split()

        if begin == 0 or not before:
            matches = sorted(c for c in state.all_first_words if c.startswith(text))
        else:
            first = before[0]
            cmd = commands.resolve(first)
            if cmd is not None:
                if len(before) == 1:
                    matches = [m for m in _arg_matches(cmd, state, text) if m.startswith(text)]
                elif cmd.arg == commands.Arg.PROMPT and before[1] in state.prompt_args:
                    matches = [a for a in state.prompt_args[before[1]] if a.startswith(text)]
                else:
                    matches = []
            elif first in state.tool_names:
                matches = _tool_arg_matches(state, first, text)
            else:
                matches = []

        return matches[idx] if idx < len(matches) else None

    return completer


def _tool_arg_matches(state: CompletionState, tool: str, text: str) -> list[str]:
    if "=" in text:
        key, _, partial = text.partition("=")
        enum = state.arg_enums.get(f"{tool}:{key}")
        if not enum:
            return []
        return sorted(f"{key}={v}" for v in enum if v.startswith(partial))
    return sorted(k for k in state.tool_args.get(tool, []) if k.startswith(text))


def show_context_help(state: CompletionState) -> None:
    """Display context-sensitive help based on the current readline buffer."""
    rl = _get_readline()
    if rl is None:
        return
    from .helptext import show_context_help_for

    show_context_help_for(state, rl.get_line_buffer())


def _get_readline() -> ReadlineModule | None:
    """Return the best available readline module, or None.

    Preference order: gnureadline (always GNU) → readline (may be libedit).
    """
    try:
        # Beware: gnureadline installs a .so but no .py or .pyi, so `ty check` won't find it.
        # Thus we tell ty to not complain about something it's failing to handle, rather than being wrong.
        import gnureadline  # ty: ignore[unresolved-import]
        return cast("ReadlineModule", gnureadline)
    except ImportError:
        pass
    try:
        import readline
        return cast("ReadlineModule", readline)
    except ImportError:
        return None


def _readline_is_libedit(rl: ReadlineModule) -> bool:
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
    # libedit historically lacks set_pre_input_hook; probe defensively even
    # though the protocol declares it.
    set_hook = getattr(rl, "set_pre_input_hook", None)
    if set_hook is None:
        return

    def _hook() -> None:
        rl.set_pre_input_hook(None)
        rl.insert_text(text)
        rl.redisplay()

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
