# Copyright 2026 Phil Pennock — see LICENSE file.

"""Choosing the protocol era and revision for a connection.

MCP has two eras: the initialize handshake (2024-11-05 to 2025-11-25) and
stateless per-request metadata (2026-07-28 onwards).  A dual-era client, per
the backward-compatibility sections of 2026-07-28 basic/versioning and both
transport pages, probes with `server/discover`:

  - a DiscoverResult, or a recognised modern error (such as
    UnsupportedProtocolVersion, which lists the supported versions), means
    the server is modern: stay modern, picking a mutual version;
  - any other error, an HTTP 4xx without a modern error body, or silence
    means a legacy server: fall back to initialize.

The fallback is never keyed to one particular error code.  Every step is
recorded in ServerState.how_chosen for the start-up report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from . import versions
from .color import dim, eprint
from .messages import JsonObject, as_obj
from .sanitize import sanitize
from .transport import (
    ConnectionClosed,
    HttpStatusError,
    NoPendingMessages,
    ReceiveTimeout,
    TransportError,
)

if TYPE_CHECKING:
    from .protocol import McpSession

EraChoice = Literal["auto", "legacy", "modern"]

# Codes only a modern server sends; seeing one means "modern, adjust and retry".
MODERN_ERROR_CODES = {
    versions.UNSUPPORTED_PROTOCOL_VERSION,
    versions.MISSING_REQUIRED_CLIENT_CAPABILITY,
    versions.HEADER_MISMATCH,
}

DEFAULT_PROBE_TIMEOUT = 5.0


@dataclass
class ConnectOptions:
    era: EraChoice = "auto"
    # Revision to offer (legacy) or request (modern); None = latest of that era.
    version: str | None = None
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT
    # Handshake era over Streamable HTTP: open the GET stream after connecting,
    # as SDK clients do, so unsolicited server messages arrive.
    listen: bool = True


class ModernRejected(ConnectionError):
    """--era modern was requested but the server is not modern."""


@dataclass
class _Probe:
    modern: bool
    reason: str


def connect(session: McpSession, options: ConnectOptions | None = None) -> None:
    """Establish the protocol over an already-open transport."""
    opts = options or ConnectOptions()
    session.connect_options = opts
    if opts.era != "legacy" and session.transport.kind != "sse":
        probe = probe_modern(session, opts)
        if probe.modern:
            return
        if opts.era == "modern":
            raise ModernRejected(f"server did not accept the stateless protocol ({probe.reason})")
        session.state.how_chosen.append(f"falling back to initialize: {probe.reason}")
        session.reset_to_handshake()
    _initialize(session, opts)


def _initialize(session: McpSession, opts: ConnectOptions) -> None:
    """initialize, falling back from Streamable HTTP to HTTP+SSE as 2025-03-26 describes."""
    try:
        session.initialize(_legacy_version(opts))
    except HttpStatusError as e:
        if session.transport.kind != "http" or e.status not in (400, 404, 405) or e.messages:
            raise
        session.state.how_chosen.append(
            f"initialize over Streamable HTTP got HTTP {e.status}; trying the 2024-11-05 HTTP+SSE transport"
        )
        switch_to_legacy_sse(session)
        session.initialize(_legacy_version(opts))
    if opts.listen and session.transport.kind == "http":
        _open_get_stream(session)
    if session.transport.kind == "sse":
        session.findings.deprecated(
            "legacy-sse-transport",
            "server uses the HTTP+SSE transport, deprecated since 2025-03-26",
            "basic/transports#backwards-compatibility",
        )


def _open_get_stream(session: McpSession) -> None:
    open_stream = getattr(session.transport, "open_listen_stream", None)
    if open_stream is None:
        return
    try:
        open_stream()
    except HttpStatusError as e:
        session.state.how_chosen.append(f"GET listening stream: not offered (HTTP {e.status})")
        return
    except TransportError as e:
        session.state.how_chosen.append(f"GET listening stream: failed ({sanitize(str(e))})")
        return
    session.state.how_chosen.append("GET listening stream: open")


def switch_to_legacy_sse(session: McpSession) -> None:
    from .transport.legacy_sse import LegacySseTransport

    old = session.transport
    url = getattr(old, "url")
    new = LegacySseTransport(
        url,
        verify_tls=getattr(old, "verify_tls", True),
        timeout=getattr(old, "timeout", 120.0),
        extra_headers=old.wire.extra_headers,
    )
    new.trace = old.trace
    new.authorization = getattr(old, "authorization", None)
    new.auth = getattr(old, "auth", None)
    old.close()
    session.transport = new
    session.state.how_chosen.append(f"HTTP+SSE endpoint: {sanitize(new.endpoint or '?')}")


def reconnect(session: McpSession) -> None:
    session.reconnect()
    connect(session, session.connect_options)


def _legacy_version(opts: ConnectOptions) -> str | None:
    if opts.version and versions.era_of(opts.version) == "handshake":
        return opts.version
    return None


def _modern_version(opts: ConnectOptions) -> str:
    if opts.version and versions.era_of(opts.version) == "modern":
        return opts.version
    return versions.LATEST_MODERN


def _choose(supported: list[str], wanted: str) -> str | None:
    """The best version we both speak, preferring the one we asked for."""
    if wanted in supported:
        return wanted
    mutual = [v for v in supported if versions.is_known(v) and versions.era_of(v) == "modern"]
    return max(mutual) if mutual else None


def probe_modern(session: McpSession, opts: ConnectOptions) -> _Probe:
    wanted = _modern_version(opts)
    for attempt in range(2):
        session.state.era = "modern"
        session.state.version = wanted
        session.apply_wire()
        req = session.build_request("server/discover", None)
        req_id = req["id"]
        resp: JsonObject | None = None
        try:
            session.transport.send(req)
            resp = session.wait_for(req_id, timeout=opts.probe_timeout)
        except HttpStatusError as e:
            resp = _modern_error_in(e.messages)
            if resp is None:
                return _Probe(False, f"HTTP {e.status} to server/discover without a modern error")
        except ReceiveTimeout:
            # A legacy server may ignore unknown pre-initialize requests; any
            # late reply must not be mistaken for something else.
            session._abandoned.add(req_id)
            return _Probe(False, f"no reply to server/discover within {opts.probe_timeout:g}s")
        except (ConnectionClosed, NoPendingMessages) as e:
            if session.transport.kind == "stdio":
                session.findings.note(
                    "exit-on-discover",
                    "server exited when sent server/discover before initialize; restarted it",
                )
                eprint(dim("  (server exited on server/discover; restarting it for initialize)"))
                session.transport.reconnect()
            return _Probe(False, f"connection closed on server/discover ({sanitize(str(e))})")
        err = as_obj(resp.get("error"))
        if not err:
            result = as_obj(resp.get("result"))
            supported = [v for v in result.get("supportedVersions", []) if isinstance(v, str)]
            chosen = _choose(supported, wanted) or wanted
            if chosen != wanted:
                note = f"server/discover: requested {wanted}; server supports {', '.join(supported)}; using {chosen}"
            else:
                note = f"server/discover: requested {wanted}; server supports {', '.join(supported) or '?'}"
            if not _choose(supported, wanted):
                session.findings.violation(
                    "discover-version-not-supported",
                    f"server/discover answered a {wanted} request but supportedVersions omits it",
                    "basic/versioning",
                )
            session.adopt_modern(chosen, result, note)
            session._observe_result("server/discover", resp)
            return _Probe(True, "")
        code = err.get("code")
        if code == versions.UNSUPPORTED_PROTOCOL_VERSION and attempt == 0:
            data = as_obj(err.get("data"))
            supported = [v for v in data.get("supported", []) if isinstance(v, str)]
            chosen = _choose(supported, wanted)
            session.state.supported = supported
            session.state.how_chosen.append(
                f"server/discover: {wanted} unsupported; server supports {', '.join(supported) or '?'}"
            )
            if chosen is None:
                return _Probe(False, "server lists no stateless revision this client knows")
            wanted = chosen
            continue
        if code in MODERN_ERROR_CODES:
            session.state.how_chosen.append(f"server/discover: modern error {code}")
            return _Probe(False, f"modern server rejected server/discover ({sanitize(str(err.get('message')))})")
        return _Probe(False, f"server/discover answered with error {sanitize(str(code))}")
    return _Probe(False, "no mutually supported stateless revision")


def _modern_error_in(msgs: list[Any]) -> JsonObject | None:
    for m in msgs:
        err = as_obj(as_obj(m).get("error"))
        if err.get("code") in MODERN_ERROR_CODES:
            return as_obj(m)
    return None
