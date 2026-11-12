# Copyright 2026 Phil Pennock — see LICENSE file.

"""The 2024-11-05 HTTP+SSE transport (deprecated since 2025-03-26).

The client GETs an SSE stream; the server's first event is `endpoint`,
naming the URL to POST messages to.  Every server message, responses
included, arrives on that one stream.  Still served by many older servers.

The endpoint must be on the same origin as the SSE URL: otherwise a server
could steer our POSTs (and any credentials) at another host.
"""

from __future__ import annotations

import queue
import time
from typing import Any
from urllib.parse import urljoin

from ..color import bold_err, eprint
from ..fetch import same_origin
from ..sanitize import sanitize
from .base import ConnectionClosed, TransportError, WireProfile, trace_info
from .http import HttpTransport, Stream
from .sse import SseEvent

ENDPOINT_TIMEOUT = 10.0


class LegacySseTransport(HttpTransport):
    kind = "sse"

    def __init__(self, url: str, **kw: Any) -> None:
        super().__init__(url, **kw)
        self.wire = WireProfile(sessions=False, extra_headers=self.wire.extra_headers)
        self.endpoint: str | None = None
        self.connect()

    def describe(self) -> str:
        return f"HTTP+SSE (2024-11-05, deprecated): {self.url} → POST {self.endpoint or '?'}"

    def post_url(self) -> str:
        if self.endpoint is None:
            raise TransportError("No endpoint event received from the SSE stream")
        return self.endpoint

    def connect(self) -> None:
        self.endpoint = None
        self.open_listen_stream()
        deadline = time.monotonic() + ENDPOINT_TIMEOUT
        while self.endpoint is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                raise TransportError(f"No endpoint event within {ENDPOINT_TIMEOUT:g}s; not an HTTP+SSE server?")
            try:
                kind, s, item = self._q.get(timeout=remaining)
            except queue.Empty:
                continue
            self._process(kind, s, item)
            if self._get_stream is None and self.endpoint is None:
                raise ConnectionClosed("SSE stream closed before the endpoint event")

    def _handle_event(self, s: Stream, ev: SseEvent) -> None:
        if ev.event != "endpoint":
            super()._handle_event(s, ev)
            return
        target = urljoin(self.url, ev.data.strip())
        if not same_origin(target, self.url):
            self.close()
            raise TransportError(
                f"Server's endpoint {sanitize(target)} is on another origin; refusing it (SSRF protection)"
            )
        if self.trace:
            trace_info(f"endpoint {sanitize(target)}")
        self.endpoint = target

    def _stream_ended(self, s: Stream, error: Exception | None) -> None:
        was_main = s is self._get_stream
        super()._stream_ended(s, error)
        if was_main and not s.abandoned:
            raise ConnectionClosed("The server closed the HTTP+SSE stream")

    def open_listen_stream(self) -> None:
        if self._get_stream is None:
            super().open_listen_stream()

    def close_listen_stream(self) -> None:
        # The SSE stream is the transport; /listen must not close it.
        pass

    def reconnect(self) -> None:
        eprint(bold_err("Reconnecting the HTTP+SSE stream..."))
        self.close()
        self.connect()
