# Copyright 2026 Phil Pennock — see LICENSE file.

"""Registry of REPL builtin commands.

One table drives dispatch, /help, and tab-completion.  Command modules
register themselves with @command; importing this package loads them all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from ..sanitize import sanitize

if TYPE_CHECKING:
    from ..repl import Repl

# Builtin command prefix.  Tool names are stripped of this prefix during
# sanitization so that a malicious server cannot shadow builtins.
BUILTIN_PREFIX = "/"


def display_tool_name(raw: str) -> str:
    """What a tool is called at the prompt: sanitized, no spaces, no builtin prefix."""
    return sanitize(raw).replace(" ", "_").lstrip(BUILTIN_PREFIX)


class Arg(StrEnum):
    """What a command's first argument is, for tab-completion."""

    NONE = "none"
    TOOL = "tool"
    PROMPT = "prompt"
    RESOURCE = "resource"
    COMMAND = "command"
    METHOD = "method"
    LEVEL = "level"
    FREE = "free"


# Display order of groups in /help.
GROUPS = ("Tools", "Resources", "Prompts", "Server", "Client", "Protocol", "Diagnostics", "Auth", "Session")

# A handler returns False to end the REPL; anything else continues.
Handler = Callable[["Repl", str], "bool | None"]


@dataclass(frozen=True)
class Command:
    name: str
    alias: str
    summary: str
    usage: str
    group: str
    arg: Arg
    func: Handler
    # Literal words accepted as the first argument (completed with Tab).
    words: tuple[str, ...] = ()


REGISTRY: dict[str, Command] = {}
ALIASES: dict[str, str] = {}


def command(
    name: str,
    *,
    summary: str,
    group: str,
    alias: str = "",
    usage: str = "",
    arg: Arg = Arg.NONE,
    words: tuple[str, ...] = (),
) -> Callable[[Handler], Handler]:
    assert name.startswith(BUILTIN_PREFIX) and group in GROUPS, name

    def register(func: Handler) -> Handler:
        if name in REGISTRY or (alias and alias in ALIASES):
            raise ValueError(f"duplicate command {name}")
        REGISTRY[name] = Command(name, alias, summary, usage or name, group, arg, func, words)
        if alias:
            ALIASES[alias] = name
        return func

    return register


def resolve(word: str) -> Command | None:
    return REGISTRY.get(ALIASES.get(word, word))


def all_words() -> set[str]:
    return set(REGISTRY) | set(ALIASES)


def by_group() -> list[tuple[str, list[Command]]]:
    out = []
    for g in GROUPS:
        cmds = sorted((c for c in REGISTRY.values() if c.group == g), key=lambda c: c.name)
        if cmds:
            out.append((g, cmds))
    return out


# Register the command modules.  Imported last: they use `command` above.
from . import client, core, diag, protocol_cmds, server, streams  # noqa: E402, F401
