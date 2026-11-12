# Copyright 2026 Phil Pennock — see LICENSE file.

"""Transport protocol, wire profile, errors and tracing shared by all transports."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ..color import cyan, dim, eprint, yellow
from ..messages import JsonObject, Payload

# Size and timeout limits.
MAX_LINE_BYTES = 16 * 1024 * 1024  # 16 MiB per JSON-RPC message
MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # 16 MiB for HTTP response bodies
HTTP_CONNECT_TIMEOUT = 10.0  # seconds to establish TCP+TLS
DEFAULT_TIMEOUT = 120.0  # seconds to wait for a reply

TransportKind = Literal["stdio", "http", "sse"]


class TransportError(Exception):
    """A transport-level failure."""


class SessionExpiredError(TransportError):
    """HTTP 404 on a request carrying a session ID: the session has gone."""


class ConnectionClosed(TransportError):
    """The peer closed the connection (EOF on stdio, stream end on HTTP)."""


class ReceiveTimeout(TransportError):
    """Nothing arrived within the timeout."""


class NoPendingMessages(TransportError):
    """HTTP: no open stream or queued message could still carry a reply."""


class HttpStatusError(TransportError):
    """An HTTP error status, with any JSON-RPC messages found in the body."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
        messages: list[object] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.headers = dict(headers or {})
        self.body = body
        self.messages = messages or []


class AuthRequiredError(HttpStatusError):
    """HTTP 401/403: the server wants (different) credentials."""

    @property
    def www_authenticate(self) -> str:
        for k, v in self.headers.items():
            if k.lower() == "www-authenticate":
                return v
        return ""


@dataclass
class WireProfile:
    """How to frame requests for the protocol revision in use.

    The session sets this after negotiation; HTTP transports read it for
    headers.  stdio has no header layer, so only `protocol_version` matters
    there (and only as information).
    """

    protocol_version: str | None = None
    # Send MCP-Protocol-Version (2025-06-18+).
    version_header: bool = False
    # Send Mcp-Method / Mcp-Name (2026-07-28+).
    method_headers: bool = False
    # Track and send Mcp-Session-Id (2025-03-26 to 2025-11-25).
    sessions: bool = True
    extra_headers: dict[str, str] = field(default_factory=dict)


class AuthProvider(Protocol):
    """Supplies and renews the Authorization header for HTTP transports."""

    def current_header(self) -> str | None: ...

    def handle_challenge(self, status: int, www_authenticate: str | None, attempt: int) -> str | None:
        """A new Authorization header after a 401/403, or None to give up."""
        ...


class Transport(Protocol):
    """A bidirectional JSON-RPC message pipe.

    The session drives it: `send` a payload, then `receive` messages until
    the reply arrives, handling notifications and server requests on the
    way.  Transports never interpret messages beyond framing.
    """

    trace: bool
    wire: WireProfile
    kind: TransportKind

    def send(self, payload: Payload, *, headers: Mapping[str, str] | None = None) -> None: ...

    def receive(self, timeout: float | None = None) -> JsonObject | Any:
        """Next incoming message.

        Raises ReceiveTimeout, ConnectionClosed, or NoPendingMessages.
        The value is whatever JSON the peer sent; the caller validates it.
        """
        ...

    def poll(self) -> list[Any]:
        """Messages that have already arrived, without blocking."""
        ...

    @property
    def session_id(self) -> str | None: ...

    def describe(self) -> str: ...

    def close(self) -> None: ...

    def reconnect(self) -> None: ...


def trace_send(payload: Payload, note: str = "") -> None:
    suffix = f"  {note}" if note else ""
    eprint(cyan(f">>> {json.dumps(payload)}{suffix}"))


def trace_recv(msg: object, note: str = "") -> None:
    suffix = f"  {note}" if note else ""
    eprint(yellow(f"<<< {json.dumps(msg)}{suffix}"))


def trace_info(text: str) -> None:
    eprint(dim(f"--- {text}"))


_SECRET_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie"}


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Copy of headers safe to print: credentials are masked."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _SECRET_HEADERS:
            scheme = v.split(" ", 1)[0] if " " in v else ""
            out[k] = f"{scheme} <redacted>".strip()
        else:
            out[k] = v
    return out


def check_header(name: str, value: str) -> None:
    """Refuse header names/values that could split or inject headers."""
    if not name or any(c in name for c in "\r\n:\0 \t"):
        raise ValueError(f"invalid header name: {name!r}")
    if any(c in value for c in "\r\n\0"):
        raise ValueError(f"invalid characters in value of header {name}")
