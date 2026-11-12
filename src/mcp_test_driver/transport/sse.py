# Copyright 2026 Phil Pennock — see LICENSE file.

"""Server-Sent Events parsing (WHATWG HTML "event stream" rules).

Shared by Streamable HTTP response streams, the HTTP GET listening stream,
and the legacy 2024-11-05 HTTP+SSE transport.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass


class SseLimitExceeded(Exception):
    pass


@dataclass
class SseEvent:
    event: str
    data: str
    id: str | None


class SseParser:
    """Incremental parser; feed it lines, collect dispatched events.

    `last_event_id` and `retry_ms` persist across events, as the spec says,
    so the caller can resume a stream (Last-Event-ID) and honour the
    server's reconnection delay.  An event with an id but no data (the
    2025-11-25 "priming" event) is not dispatched but does update
    last_event_id; `primed` records that one was seen.
    """

    def __init__(self, max_bytes: int) -> None:
        self._max = max_bytes
        self.total_bytes = 0
        self.last_event_id: str | None = None
        self.retry_ms: int | None = None
        self.primed = False
        self._data: list[str] = []
        self._event = ""
        self._id_in_event = False

    def feed(self, raw: bytes) -> SseEvent | None:
        self.total_bytes += len(raw)
        if self.total_bytes > self._max:
            raise SseLimitExceeded(f"SSE stream exceeds {self._max} bytes")
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if name == "data":
            self._data.append(value)
        elif name == "event":
            self._event = value
        elif name == "id":
            if "\0" not in value:
                self.last_event_id = value
                self._id_in_event = True
        elif name == "retry":
            if value.isdigit():
                self.retry_ms = int(value)
        return None

    def finish(self) -> SseEvent | None:
        """End of stream: a trailing event without a blank line still counts.

        Strictly the spec discards it, but servers commonly omit the final
        blank line and the message is more useful than the pedantry.
        """
        return self._dispatch()

    def _dispatch(self) -> SseEvent | None:
        # Per the spec an empty data buffer dispatches nothing, even when a
        # "data:" line was present; that is exactly the priming event.
        if not "".join(self._data):
            if self._id_in_event:
                self.primed = True
            self._data = []
            self._event = ""
            self._id_in_event = False
            return None
        ev = SseEvent(
            event=self._event or "message",
            data="\n".join(self._data),
            id=self.last_event_id if self._id_in_event else None,
        )
        self._data = []
        self._event = ""
        self._id_in_event = False
        return ev


def iter_events(lines: Iterable[bytes], parser: SseParser) -> Iterator[SseEvent]:
    for raw in lines:
        ev = parser.feed(raw)
        if ev is not None:
            yield ev
    ev = parser.finish()
    if ev is not None:
        yield ev
