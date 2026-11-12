# Copyright 2026 Phil Pennock — see LICENSE file.

"""Choosing the protocol era and revision for a connection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .protocol import McpSession

EraChoice = Literal["auto", "legacy", "modern"]


@dataclass
class ConnectOptions:
    era: EraChoice = "auto"
    # Version to offer (legacy) or to request (modern); None = latest of that era.
    version: str | None = None


def connect(session: McpSession, options: ConnectOptions | None = None) -> None:
    """Establish the protocol over an already-open transport."""
    opts = options or ConnectOptions()
    session.connect_options = opts
    session.initialize(opts.version)


def reconnect(session: McpSession) -> None:
    session.reconnect()
    connect(session, session.connect_options)
