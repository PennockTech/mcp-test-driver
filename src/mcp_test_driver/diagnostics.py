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

from .color import dim, red, yellow


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
    # Index of the first finding not yet shown to the user, plus any later
    # ones already printed where they happened.
    _shown: int = 0
    _preshown: set[int] = field(default_factory=set)

    def add(
        self,
        level: Level,
        kind: Kind,
        code: str,
        message: str,
        spec_ref: str = "",
        *,
        shown: bool = False,
    ) -> Finding | None:
        key = (code, message)
        if key in self._seen:
            return None
        self._seen.add(key)
        f = Finding(level, kind, code, message, spec_ref)
        if shown:
            self._preshown.add(len(self.items))
        self.items.append(f)
        return f

    def violation(self, code: str, message: str, spec_ref: str = "", *, level: Level = Level.ERROR) -> None:
        self.add(level, Kind.VIOLATION, code, message, spec_ref)

    def beyond(self, code: str, message: str, spec_ref: str = "") -> None:
        self.add(Level.INFO, Kind.BEYOND_CLAIMED, code, message, spec_ref)

    def deprecated(self, code: str, message: str, spec_ref: str = "") -> None:
        self.add(Level.WARNING, Kind.DEPRECATED, code, message, spec_ref)

    def note(self, code: str, message: str, spec_ref: str = "", *, shown: bool = False) -> None:
        self.add(Level.INFO, Kind.NOTE, code, message, spec_ref, shown=shown)

    def take_unshown(self) -> list[Finding]:
        new = [f for i, f in enumerate(self.items[self._shown :], self._shown) if i not in self._preshown]
        self._shown = len(self.items)
        return new

    def clear(self) -> None:
        self.items.clear()
        self._seen.clear()
        self._preshown.clear()
        self._shown = 0

    def __iter__(self) -> Iterator[Finding]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def worst(self) -> Level | None:
        return max((f.level for f in self.items), default=None)


def print_findings(findings: list[Finding]) -> None:
    for f in findings:
        colour = red if f.level >= Level.ERROR else yellow if f.level >= Level.WARNING else dim
        ref = f"  ({f.spec_ref})" if f.spec_ref else ""
        print(colour(f"  [{f.label()}] {f.message}{ref}"))
