# Copyright 2026 Phil Pennock — see LICENSE file.

"""MCP Streamable HTTP transport (2025-03-26 onwards).

Each client message is a POST.  The reply is 202 (nothing to say), a JSON
body, or an SSE stream which may carry notifications and server requests
before the response.  Streams stay open between `receive()` calls so the
session can answer a server request while the original call is pending.

Security: redirects are refused (SSRF), bodies and streams are size-capped,
TLS is verified unless explicitly disabled, header values are checked for
CR/LF injection, and the session ID is restricted to visible ASCII.
"""

from __future__ import annotations

import base64
import json
import re
from collections import deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import urllib3
import urllib3.exceptions
import urllib3.util

from ..color import bold_err, eprint, red
from ..messages import Payload, expand
from ..sanitize import sanitize
from .base import (
    DEFAULT_TIMEOUT,
    HTTP_CONNECT_TIMEOUT,
    MAX_RESPONSE_BYTES,
    AuthRequiredError,
    ConnectionClosed,
    HttpStatusError,
    NoPendingMessages,
    ReceiveTimeout,
    SessionExpiredError,
    TransportError,
    WireProfile,
    check_header,
    trace_info,
    trace_recv,
    trace_send,
)
from .sse import SseEvent, SseLimitExceeded, SseParser, iter_events

# Header-safe characters for Mcp-Name / Mcp-Param-* values (SEP-2243): plain
# printable ASCII without leading/trailing space.  Anything else, or text that
# looks like our sentinel, is sent base64-encoded.
_HEADER_SAFE = re.compile(r"[\x21-\x7e]([\x20-\x7e]*[\x21-\x7e])?")
_B64_PREFIX = "=?base64?"


def encode_header_value(value: str) -> str:
    if _HEADER_SAFE.fullmatch(value) and not value.startswith(_B64_PREFIX):
        return value
    return _B64_PREFIX + base64.b64encode(value.encode()).decode() + "?="


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lname = name.lower()
    for k, v in headers.items():
        if k.lower() == lname:
            return v
    return None


def _iter_lines(resp: Any) -> Iterator[bytes]:
    """Lines from a streaming response as soon as each arrives.

    urllib3's own line iterator reads 64 KiB at a time and blocks until it
    has them, which stalls a live SSE stream; read1() returns what is there.
    """
    buf = b""
    while True:
        chunk = resp.read1(65536)
        if not chunk:
            break
        buf += chunk
        while (i := buf.find(b"\n")) >= 0:
            yield buf[: i + 1]
            buf = buf[i + 1 :]
    if buf:
        yield buf


@dataclass
class _Stream:
    resp: Any  # urllib3.BaseHTTPResponse
    parser: SseParser
    events: Iterator[SseEvent]
    origin: str  # "POST <method>" or "GET"


class HttpTransport:
    kind = "http"

    def __init__(
        self,
        url: str,
        *,
        verify_tls: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.url = url
        self.trace = True
        self.timeout = timeout
        self.wire = WireProfile()
        for k, v in (extra_headers or {}).items():
            check_header(k, v)
        self.wire.extra_headers.update(extra_headers or {})
        # Authorization is kept apart from extra_headers so OAuth can swap it.
        self.authorization: str | None = None
        self._session_id: str | None = None
        self._inbox: deque[Any] = deque()
        self._streams: deque[_Stream] = deque()
        self.batches_received = 0
        self.last_status: int | None = None
        self.last_headers: dict[str, str] = {}
        self._pool = urllib3.PoolManager(
            retries=urllib3.util.Retry(total=3, redirect=0),
            timeout=urllib3.util.Timeout(connect=HTTP_CONNECT_TIMEOUT, read=timeout),
            cert_reqs="CERT_REQUIRED" if verify_tls else "CERT_NONE",
        )

    # ── headers ────────────────────────────────────────────────────────

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def describe(self) -> str:
        return f"Streamable HTTP: {self.url}"

    def base_headers(self) -> dict[str, str]:
        h = dict(self.wire.extra_headers)
        if self.authorization:
            h["Authorization"] = self.authorization
        if self.wire.sessions and self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        if self.wire.version_header and self.wire.protocol_version:
            h["MCP-Protocol-Version"] = self.wire.protocol_version
        return h

    def _post_headers(self, payload: Payload, extra: Mapping[str, str] | None) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        h.update(self.base_headers())
        if self.wire.method_headers and isinstance(payload, dict):
            method = payload.get("method")
            if isinstance(method, str):
                h["Mcp-Method"] = method
                params = payload.get("params")
                if isinstance(params, dict) and method in (
                    "tools/call",
                    "prompts/get",
                    "resources/read",
                ):
                    target = params.get("uri" if method == "resources/read" else "name")
                    if isinstance(target, str):
                        h["Mcp-Name"] = encode_header_value(target)
        for k, v in (extra or {}).items():
            check_header(k, v)
            h[k] = v
        return h

    # ── sending ────────────────────────────────────────────────────────

    def _request(self, method: str, headers: dict[str, str], body: bytes | None) -> Any:
        try:
            return self._pool.request(
                method,
                self.url,
                body=body,
                headers=headers,
                preload_content=False,
                redirect=False,
            )
        except urllib3.exceptions.MaxRetryError as e:
            reason = e.reason
            if isinstance(reason, urllib3.exceptions.SSLError):
                raise TransportError(
                    f"TLS/certificate error connecting to {self.url}: {reason}"
                ) from None
            raise TransportError(f"Connection failed: {reason}") from None
        except urllib3.exceptions.SSLError as e:
            raise TransportError(f"TLS/certificate error: {e}") from None
        except urllib3.exceptions.HTTPError as e:
            raise TransportError(f"HTTP error: {e}") from None

    def send(self, payload: Payload, *, headers: Mapping[str, str] | None = None) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        if self.trace:
            trace_send(payload)
        h = self._post_headers(payload, headers)
        resp = self._request("POST", h, body)
        origin = "POST"
        if isinstance(payload, dict) and isinstance(payload.get("method"), str):
            origin = f"POST {payload['method']}"
        self._handle_response(resp, origin)

    def send_bytes(self, data: bytes) -> None:
        """POST raw bytes as the body, for malformed-input testing."""
        if self.trace:
            trace_info(f"POST raw body: {sanitize(data.decode('utf-8', errors='replace'))}")
        resp = self._request("POST", self._post_headers({}, None), data)
        self._handle_response(resp, "POST raw")

    def _handle_response(self, resp: Any, origin: str) -> None:
        keep_open = False
        try:
            self.last_status = resp.status
            self.last_headers = dict(resp.headers)
            if self.trace:
                ctype = resp.headers.get("Content-Type", "")
                trace_info(f"HTTP {resp.status} {ctype}".rstrip())
            self._check_status(resp)
            new_session_id = resp.headers.get("Mcp-Session-Id")
            if new_session_id and self.wire.sessions:
                # Per spec, session IDs are visible ASCII (0x21-0x7E).  Strip
                # anything else so it can't inject headers when echoed back.
                self._session_id = re.sub(r"[^\x21-\x7e]", "", new_session_id)
            ctype = resp.headers.get("Content-Type", "")
            if "text/event-stream" in ctype:
                parser = SseParser(MAX_RESPONSE_BYTES)
                self._streams.append(
                    _Stream(resp, parser, iter_events(_iter_lines(resp), parser), origin)
                )
                keep_open = True
            elif "application/json" in ctype:
                self._read_json_body(resp)
            elif resp.status == 202 or not ctype:
                resp.read()
            else:
                data = resp.read(1024)
                raise TransportError(
                    f"Unexpected Content-Type {sanitize(ctype)!r} "
                    f"(status {resp.status}): {sanitize(repr(data[:200]))}"
                )
        finally:
            if not keep_open:
                resp.release_conn()

    def _check_status(self, resp: Any) -> None:
        status = resp.status
        if 300 <= status < 400:
            location = sanitize(resp.headers.get("Location", "(none)"))
            resp.read()
            raise TransportError(
                f"Server returned redirect {status} to {location}; "
                f"redirects are not followed for security"
            )
        if status < 400:
            return
        body = resp.read(64 * 1024)
        messages: list[object] = []
        if "json" in resp.headers.get("Content-Type", ""):
            try:
                messages, _ = expand(json.loads(body))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        if self.trace and messages:
            for m in messages:
                trace_recv(m, f"(HTTP {status})")
        headers = dict(resp.headers)
        if status == 404 and self._session_id:
            self._session_id = None
            raise SessionExpiredError(
                "Session expired (HTTP 404); re-initialize with /reconnect"
            )
        preview = sanitize(repr(body[:200]))
        if status in (401, 403):
            raise AuthRequiredError(
                status,
                f"HTTP {status} from server: {preview}",
                headers=headers,
                body=body,
                messages=messages,
            )
        raise HttpStatusError(
            status,
            f"HTTP {status} from server: {preview}",
            headers=headers,
            body=body,
            messages=messages,
        )

    def _read_json_body(self, resp: Any) -> None:
        data = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise TransportError(f"Response body exceeds {MAX_RESPONSE_BYTES} bytes")
        if not data.strip():
            return
        try:
            obj = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            eprint(red(f"Malformed JSON from server: {e}"))
            eprint(red(f"  Raw body: {sanitize(repr(data[:200]))}"))
            return
        self._enqueue(obj)

    def _enqueue(self, obj: object) -> None:
        if self.trace:
            trace_recv(obj)
        items, is_batch = expand(obj)
        if is_batch:
            self.batches_received += 1
        self._inbox.extend(items)

    # ── receiving ──────────────────────────────────────────────────────

    def receive(self, timeout: float | None = None) -> Any:
        while True:
            if self._inbox:
                return self._inbox.popleft()
            if not self._streams:
                raise NoPendingMessages("No open response stream from server")
            self._pump(self._streams[0])

    def _pump(self, stream: _Stream) -> None:
        """Read one event from a stream into the inbox, or retire the stream."""
        try:
            ev = next(stream.events, None)
        except urllib3.exceptions.ReadTimeoutError:
            raise ReceiveTimeout(f"Server sent nothing for {self.timeout:g}s") from None
        except SseLimitExceeded as e:
            self._retire(stream)
            raise TransportError(str(e)) from None
        except (urllib3.exceptions.ProtocolError, OSError) as e:
            self._retire(stream)
            raise ConnectionClosed(f"Response stream broke: {e}") from None
        if ev is None:
            self._retire(stream)
            return
        if ev.event != "message":
            if self.trace:
                trace_info(f"SSE event {sanitize(ev.event)!r} ignored")
            return
        try:
            obj = json.loads(ev.data)
        except json.JSONDecodeError as e:
            eprint(red(f"Malformed JSON in SSE event: {e}"))
            eprint(red(f"  Payload: {sanitize(repr(ev.data[:200]))}"))
            return
        self._enqueue(obj)

    def _retire(self, stream: _Stream) -> None:
        try:
            self._streams.remove(stream)
        except ValueError:
            pass
        try:
            stream.resp.release_conn()
        except Exception:
            pass

    def poll(self) -> list[Any]:
        out = list(self._inbox)
        self._inbox.clear()
        return out

    def has_pending(self) -> bool:
        return bool(self._inbox or self._streams)

    def abandon_streams(self) -> None:
        """Drop open response streams (after Ctrl-C or a fatal error)."""
        for s in list(self._streams):
            self._retire(s)

    # ── lifecycle ──────────────────────────────────────────────────────

    def close(self) -> None:
        self.abandon_streams()
        # Per spec (2025-03-26 to 2025-11-25), DELETE ends the session.
        if self._session_id and self.wire.sessions:
            try:
                resp = self._request("DELETE", self.base_headers(), None)
                resp.read()
                resp.release_conn()
            except Exception:
                pass  # best-effort; we're closing anyway
        self._session_id = None
        self._pool.clear()

    def reconnect(self) -> None:
        eprint(bold_err("Starting a new HTTP session..."))
        self.close()
