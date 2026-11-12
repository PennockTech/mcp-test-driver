# Copyright 2026 Phil Pennock — see LICENSE file.

"""Conformance findings collected while talking to a server.

Findings annotate; they never stop the user doing anything.  Each one says
what was seen, how it relates to the revision the server claims, and where
in the spec to look.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum


class Level(IntEnum):
    INFO = 0
    WARNING = 1
    ERROR = 2


class Kind(StrEnum):
    VIOLATION = "violation"
    BEYOND_CLAIMED = "beyond-claimed"
    DEPRECATED = "deprecated"
    NOTE = "note"


@dataclass(frozen=True)
class Finding:
    level: Level
    kind: Kind
    code: str
    message: str
    spec_ref: str = ""

    def label(self) -> str:
        return f"{self.level.name.lower()}/{self.kind}"


@dataclass
class DiagnosticLog:
    """Ordered, de-duplicated findings for one connection."""

    items: list[Finding] = field(default_factory=list)
    _seen: set[tuple[str, str]] = field(default_factory=set)
    # Index of the first finding not yet shown to the user.
    _shown: int = 0

    def add(
        self,
        level: Level,
        kind: Kind,
        code: str,
        message: str,
        spec_ref: str = "",
    ) -> Finding | None:
        key = (code, message)
        if key in self._seen:
            return None
        self._seen.add(key)
        f = Finding(level, kind, code, message, spec_ref)
        self.items.append(f)
        return f

    def violation(self, code: str, message: str, spec_ref: str = "", *, level: Level = Level.ERROR) -> None:
        self.add(level, Kind.VIOLATION, code, message, spec_ref)

    def beyond(self, code: str, message: str, spec_ref: str = "") -> None:
        self.add(Level.INFO, Kind.BEYOND_CLAIMED, code, message, spec_ref)

    def deprecated(self, code: str, message: str, spec_ref: str = "") -> None:
        self.add(Level.WARNING, Kind.DEPRECATED, code, message, spec_ref)

    def note(self, code: str, message: str, spec_ref: str = "") -> None:
        self.add(Level.INFO, Kind.NOTE, code, message, spec_ref)

    def take_unshown(self) -> list[Finding]:
        new = self.items[self._shown :]
        self._shown = len(self.items)
        return new

    def clear(self) -> None:
        self.items.clear()
        self._seen.clear()
        self._shown = 0

    def __iter__(self) -> Iterator[Finding]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def worst(self) -> Level | None:
        return max((f.level for f in self.items), default=None)
