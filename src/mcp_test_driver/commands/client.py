# Copyright 2026 Phil Pennock — see LICENSE file.

"""Builtins for the client side: which capabilities we declare."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..color import bold, dim, red
from ..sanitize import safe_json
from . import command

if TYPE_CHECKING:
    from ..repl import Repl

_REQUIRES_RECONNECT = "Handshake-era servers see this after /reconnect."


def _status(repl: Repl) -> None:
    caps = repl.session.client_capabilities()
    print(f"  {bold('capabilities')}: {safe_json(caps, indent=None) if caps else dim('(none)')}")
    flags = []
    if repl.session.progress:
        flags.append("progress tokens")
    if repl.session.otel:
        flags.append("OpenTelemetry traceparent")
    if flags:
        print(f"  {bold('per-request')}: {', '.join(flags)}")


@command("/client", group="Client", summary="Show the capabilities and per-request options we send")
def cmd_client(repl: Repl, rest: str) -> None:
    _status(repl)


@command(
    "/elicitation",
    group="Client",
    summary="Answer server elicitation requests interactively",
    usage="/elicitation on [form|url|form,url]\n/elicitation off",
    words=("on", "off"),
)
def cmd_elicitation(repl: Repl, rest: str) -> None:
    from ..completion import suspend_completion
    from ..interactive import ConsolePrompter

    words = rest.split()
    if not words:
        _status(repl)
        return
    if words[0] == "off":
        repl.session.disable("elicitation/create")
    elif words[0] == "on":
        modes = frozenset((words[1] if len(words) > 1 else "form,url").split(","))
        if not modes <= {"form", "url"}:
            print(red("Modes are form and/or url."))
            return
        repl.session.enable_elicitation(ConsolePrompter(suspend_completion), modes)
    else:
        print(red("Usage: /elicitation on [modes] | off"))
        return
    _status(repl)
    print(dim(_REQUIRES_RECONNECT))


@command(
    "/sampling",
    group="Client",
    summary="Answer server sampling requests yourself (you play the LLM)",
    usage="/sampling on|off",
    words=("on", "off"),
)
def cmd_sampling(repl: Repl, rest: str) -> None:
    from ..completion import suspend_completion
    from ..interactive import ConsolePrompter

    word = rest.strip()
    if word == "on":
        repl.session.enable_sampling(ConsolePrompter(suspend_completion))
    elif word == "off":
        repl.session.disable("sampling/createMessage")
    elif word:
        print(red("Usage: /sampling on|off"))
        return
    _status(repl)
    if word:
        print(dim(_REQUIRES_RECONNECT))


@command(
    "/progress",
    group="Client",
    summary="Attach progress tokens to calls, so servers can report progress",
    usage="/progress on|off",
    words=("on", "off"),
)
def cmd_progress(repl: Repl, rest: str) -> None:
    word = rest.strip()
    if word in ("on", "off"):
        repl.session.progress = word == "on"
    elif word:
        print(red("Usage: /progress on|off"))
        return
    print(dim(f"Progress tokens {'on' if repl.session.progress else 'off'}."))


@command(
    "/otel",
    group="Client",
    summary="Send an OpenTelemetry traceparent with each request (printed, for correlation)",
    usage="/otel on|off",
    words=("on", "off"),
)
def cmd_otel(repl: Repl, rest: str) -> None:
    word = rest.strip()
    if word in ("on", "off"):
        repl.session.otel = word == "on"
    elif word:
        print(red("Usage: /otel on|off"))
        return
    print(dim(f"traceparent {'on' if repl.session.otel else 'off'}."))
