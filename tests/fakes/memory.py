"""An in-memory Transport for unit tests that need no server process."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from mcp_test_driver.transport import ConnectionClosed, WireProfile

Responder = Callable[[Any], list[Any]]


class MemoryTransport:
    """Records what is sent; answers via a responder function or a queue."""

    kind = "stdio"

    def __init__(self, responder: Responder | None = None) -> None:
        self.trace = False
        self.wire = WireProfile(sessions=False)
        self.sent: list[Any] = []
        self.inbox: deque[Any] = deque()
        self.responder = responder
        self.closed = False
        self.reconnects = 0

    @property
    def session_id(self) -> str | None:
        return None

    def describe(self) -> str:
        return "memory"

    def send(self, payload: Any, *, headers: Mapping[str, str] | None = None) -> None:
        self.sent.append(payload)
        if self.responder is not None:
            self.inbox.extend(self.responder(payload))

    def receive(self, timeout: float | None = None) -> Any:
        if not self.inbox:
            raise ConnectionClosed("memory transport is empty")
        return self.inbox.popleft()

    def poll(self) -> list[Any]:
        out = list(self.inbox)
        self.inbox.clear()
        return out

    def close(self) -> None:
        self.closed = True

    def reconnect(self) -> None:
        self.reconnects += 1
