# Copyright 2026 Phil Pennock — see LICENSE file.

"""Session-level builtins: help, quit, tracing, reconnecting, the cache."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..color import dim
from . import Arg, command

if TYPE_CHECKING:
    from ..repl import Repl


@command("/help", alias="/h", group="Session", arg=Arg.COMMAND,
         summary="Show help (or /help <tool|command>)")
def cmd_help(repl: Repl, rest: str) -> None:
    from ..completion import schedule_restore_input
    from ..helptext import show_context_help_for, show_general_help

    if rest.strip():
        show_context_help_for(repl.cache.completion, rest)
        # The F1 macro submits "/help <line>"; put the line back afterwards.
        schedule_restore_input(rest)
    else:
        show_general_help()


@command("/quit", alias="/q", group="Session", summary="Exit")
def cmd_quit(repl: Repl, rest: str) -> bool:
    return False


@command("/trace", alias="/t", group="Session", summary="Toggle JSON-RPC protocol tracing",
         usage="/trace [on|off]", words=("on", "off"))
def cmd_trace(repl: Repl, rest: str) -> None:
    arg = rest.strip().lower()
    if arg in ("on", "off"):
        repl.trace_enabled = arg == "on"
    else:
        repl.trace_enabled = not repl.trace_enabled
    repl.session.transport.trace = repl.trace_enabled
    print(dim(f"Protocol tracing {'on' if repl.trace_enabled else 'off'}."))


@command("/reconnect", alias="/rc", group="Session",
         summary="Reconnect and re-negotiate with the server")
def cmd_reconnect(repl: Repl, rest: str) -> None:
    from ..negotiate import reconnect

    reconnect(repl.session)
    repl.cache.fetch_advertised(repl.session)
    repl.refresh_readline()
    print(dim(f"Reconnected. {len(repl.cache.tools)} tools available."))


@command("/cache-flush", alias="/cf", group="Session",
         summary="Re-fetch tools, resources and prompts from the server")
def cmd_cache_flush(repl: Repl, rest: str) -> None:
    repl.cache.fetch_advertised(repl.session)
    repl.refresh_readline()
    print(dim(f"Cache flushed. {len(repl.cache.tools)} tools available."))
