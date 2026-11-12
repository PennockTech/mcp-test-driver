# Copyright 2026 Phil Pennock — see LICENSE file.

"""MCP Streamable HTTP transport (2025-03-26 onwards).

Each client message is a POST.  The reply is 202 (nothing to say), a JSON
body, or an SSE stream which may carry notifications and server requests
before the response.  In 2025-03-26 to 2025-11-25 a GET opens a listening
stream for unsolicited server messages, and broken streams resume with
Last-Event-ID; 2026-07-28 drops both.

Every open SSE stream gets a reader thread that only parses and queues; the
main thread takes items off the queue, so it alone prints, and receive()
honours its timeout however many streams are open.

Security: redirects are refused (SSRF), bodies and streams are size-capped,
TLS is verified unless explicitly disabled, header values are checked for
CR/LF injection, and the session ID is restricted to visible ASCII.
"""

from __future__ import annotations

import base64
import json
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
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
# Limits on Last-Event-ID resumption, so a flapping server can't loop us.
_MAX_RESUMES = 5
_MAX_RETRY_WAIT = 30.0


def encode_header_value(value: str) -> str:
    if _HEADER_SAFE.fullmatch(value) and not value.startswith(_B64_PREFIX):
        return value
    return _B64_PREFIX + base64.b64encode(value.encode()).decode() + "?="


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


@dataclass(eq=False)
class Stream:
    resp: Any  # urllib3 BaseHTTPResponse
    parser: SseParser
    origin: str  # "POST tools/call", "GET", ...
    # The request this POST stream must answer, for resumption decisions.
    request_id: Any = None
    answered: bool = False
    abandoned: bool = False
    resumes: int = 0
    events: int = 0
    done: threading.Event = field(default_factory=threading.Event)


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
        self._q: queue.Queue[tuple[str, Any, Any]] = queue.Queue()
        self._streams: set[Stream] = set()
        self._get_stream: Stream | None = None
        self.batches_received = 0
        self.resumptions = 0
        self.primed_streams = 0
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
                if isinstance(params, dict) and method in ("tools/call", "prompts/get", "resources/read"):
                    target = params.get("uri" if method == "resources/read" else "name")
                    if isinstance(target, str):
                        h["Mcp-Name"] = encode_header_value(target)
        for k, v in (extra or {}).items():
            check_header(k, v)
            h[k] = v
        return h

    # ── sending ────────────────────────────────────────────────────────

    def _request(
        self, method: str, headers: dict[str, str], body: bytes | None, *, url: str | None = None,
        read_timeout: float | None | bool = False,
    ) -> Any:
        kw: dict[str, Any] = {}
        if read_timeout is not False:
            kw["timeout"] = urllib3.util.Timeout(connect=HTTP_CONNECT_TIMEOUT, read=read_timeout)
        try:
            return self._pool.request(
                method,
                url or self.url,
                body=body,
                headers=headers,
                preload_content=False,
                redirect=False,
                **kw,
            )
        except urllib3.exceptions.MaxRetryError as e:
            reason = e.reason
            if isinstance(reason, urllib3.exceptions.SSLError):
                raise TransportError(f"TLS/certificate error connecting to {url or self.url}: {reason}") from None
            raise TransportError(f"Connection failed: {reason}") from None
        except urllib3.exceptions.SSLError as e:
            raise TransportError(f"TLS/certificate error: {e}") from None
        except urllib3.exceptions.HTTPError as e:
            raise TransportError(f"HTTP error: {e}") from None

    def post_url(self) -> str:
        return self.url

    def send(self, payload: Payload, *, headers: Mapping[str, str] | None = None) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        if self.trace:
            trace_send(payload)
        resp = self._request("POST", self._post_headers(payload, headers), body, url=self.post_url())
        origin = "POST"
        request_id = None
        if isinstance(payload, dict) and isinstance(payload.get("method"), str):
            origin = f"POST {payload['method']}"
            request_id = payload.get("id")
        self._handle_response(resp, origin, request_id)

    def send_bytes(self, data: bytes) -> None:
        """POST raw bytes as the body, for malformed-input testing."""
        if self.trace:
            trace_info(f"POST raw body: {sanitize(data.decode('utf-8', errors='replace'))}")
        resp = self._request("POST", self._post_headers({}, None), data, url=self.post_url())
        self._handle_response(resp, "POST raw", None)

    def _handle_response(self, resp: Any, origin: str, request_id: Any) -> None:
        keep_open = False
        try:
            self.last_status = resp.status
            self.last_headers = dict(resp.headers)
            if self.trace:
                trace_info(f"HTTP {resp.status} {resp.headers.get('Content-Type', '')}".rstrip())
            self._check_status(resp)
            new_session_id = resp.headers.get("Mcp-Session-Id")
            if new_session_id and self.wire.sessions:
                # Per spec, session IDs are visible ASCII (0x21-0x7E).  Strip
                # anything else so it can't inject headers when echoed back.
                self._session_id = re.sub(r"[^\x21-\x7e]", "", new_session_id)
            ctype = resp.headers.get("Content-Type", "")
            if "text/event-stream" in ctype:
                self._start_stream(resp, origin, request_id)
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
                f"Server returned redirect {status} to {location}; redirects are not followed for security"
            )
        if status < 400:
            return
        body = resp.read(64 * 1024)
        messages: list[Any] = []
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
            raise SessionExpiredError("Session expired (HTTP 404); re-initialize with /reconnect")
        preview = sanitize(repr(body[:200]))
        cls = AuthRequiredError if status in (401, 403) else HttpStatusError
        raise cls(status, f"HTTP {status} from server: {preview}", headers=headers, body=body, messages=messages)

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

    def _enqueue(self, obj: Any) -> None:
        if self.trace:
            trace_recv(obj)
        items, is_batch = expand(obj)
        if is_batch:
            self.batches_received += 1
        self._inbox.extend(items)

    # ── streams ────────────────────────────────────────────────────────

    def _start_stream(self, resp: Any, origin: str, request_id: Any, *, parser: SseParser | None = None,
                      resumes: int = 0) -> Stream:
        s = Stream(resp, parser or SseParser(MAX_RESPONSE_BYTES), origin, request_id, resumes=resumes)
        self._streams.add(s)
        threading.Thread(target=self._read_stream, args=(s,), daemon=True).start()
        return s

    def _read_stream(self, s: Stream) -> None:
        """Reader thread: parse events, queue them; never prints."""
        try:
            for ev in iter_events(_iter_lines(s.resp), s.parser):
                if s.abandoned:
                    return
                s.events += 1
                if ev.event != "message":
                    self._q.put(("event", s, ev))
                    continue
                try:
                    obj = json.loads(ev.data)
                except json.JSONDecodeError as e:
                    self._q.put(("warn", s, f"Malformed JSON in SSE event: {e}; payload {ev.data[:200]!r}"))
                    continue
                if s.request_id is not None and isinstance(obj, dict) and obj.get("id") == s.request_id \
                        and "method" not in obj:
                    s.answered = True
                self._q.put(("msg", s, obj))
        except SseLimitExceeded as e:
            if not s.abandoned:
                self._q.put(("error", s, TransportError(str(e))))
        except urllib3.exceptions.ReadTimeoutError:
            if not s.abandoned:
                self._q.put(("error", s, ReceiveTimeout(f"Server sent nothing for {self.timeout:g}s")))
        except Exception as e:
            # Closing a response from the main thread (abandon) races with
            # http.client inside read1(), which can then raise almost
            # anything (AttributeError on a cleared socket, ValueError, ...).
            if not s.abandoned:
                self._q.put(("broken", s, e))
        finally:
            s.done.set()
            self._q.put(("end", s, None))

    def _handle_event(self, s: Stream, ev: SseEvent) -> None:
        """Non-"message" SSE events.  Subclasses (legacy SSE) override."""
        if self.trace:
            trace_info(f"SSE event {sanitize(ev.event)!r} ignored")

    def _stream_ended(self, s: Stream, error: Exception | None) -> None:
        self._streams.discard(s)
        if s is self._get_stream:
            self._get_stream = None
        try:
            s.resp.release_conn()
        except Exception:
            pass
        if s.abandoned:
            return
        if s.parser.primed:
            self.primed_streams += 1
        if s.request_id is not None and not s.answered:
            if self._can_resume(s):
                self._resume(s)
                return
            if error is not None:
                raise ConnectionClosed(f"Response stream for {s.origin} broke: {error}") from None
            if not self._streams:
                raise NoPendingMessages(f"Server closed the {s.origin} stream without answering")

    def _can_resume(self, s: Stream) -> bool:
        # Resumption (2025-03-26 to 2025-11-25) needs an event id to resume from.
        return self.wire.sessions and s.parser.last_event_id is not None and s.resumes < _MAX_RESUMES

    def _resume(self, s: Stream) -> None:
        wait = min((s.parser.retry_ms or 0) / 1000, _MAX_RETRY_WAIT)
        if self.trace:
            trace_info(f"resuming {s.origin} stream from event {sanitize(s.parser.last_event_id or '')}"
                       + (f" after {wait:g}s" if wait else ""))
        if wait:
            time.sleep(wait)
        self.resumptions += 1
        headers = {"Accept": "text/event-stream", **self.base_headers()}
        headers["Last-Event-ID"] = s.parser.last_event_id or ""
        resp = self._request("GET", headers, None)
        try:
            self._check_status(resp)
        except Exception:
            resp.release_conn()
            raise
        parser = SseParser(MAX_RESPONSE_BYTES)
        parser.last_event_id = s.parser.last_event_id
        self._start_stream(resp, s.origin + " (resumed)", s.request_id, parser=parser, resumes=s.resumes + 1)

    # ── receiving ──────────────────────────────────────────────────────

    def _process(self, kind: str, s: Stream, item: Any) -> None:
        if kind == "msg":
            if not s.abandoned:
                self._enqueue(item)
        elif kind == "event":
            self._handle_event(s, item)
        elif kind == "warn":
            eprint(red(sanitize(item)))
        elif kind == "error":
            self._streams.discard(s)
            raise item
        elif kind == "broken":
            s.done.set()
            self._stream_ended(s, item)
        elif kind == "end":
            if s in self._streams:
                self._stream_ended(s, None)

    def receive(self, timeout: float | None = None) -> Any:
        wait = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + wait
        while True:
            if self._inbox:
                return self._inbox.popleft()
            if not self._streams and self._q.empty():
                raise NoPendingMessages("No open response stream from server")
            remaining = deadline - time.monotonic()
            try:
                kind, s, item = self._q.get(timeout=max(remaining, 0)) if remaining > 0 else self._q.get_nowait()
            except queue.Empty:
                raise ReceiveTimeout(f"Server sent nothing for {wait:g}s") from None
            self._process(kind, s, item)

    def poll(self) -> list[Any]:
        while True:
            try:
                kind, s, item = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                self._process(kind, s, item)
            except TransportError as e:
                eprint(red(f"Stream error: {e}"))
        out = list(self._inbox)
        self._inbox.clear()
        return out

    def abandon_streams(self) -> None:
        """Drop open POST response streams (after Ctrl-C, a timeout, or close)."""
        for s in list(self._streams):
            if s is not self._get_stream:
                self._abandon(s)

    def _abandon(self, s: Stream) -> None:
        s.abandoned = True
        self._streams.discard(s)
        shutdown = getattr(s.resp, "shutdown", None)
        try:
            if shutdown is not None:
                shutdown()
            s.resp.release_conn()
        except Exception:
            pass

    # ── GET listening stream (2025-03-26 to 2025-11-25) ────────────────

    def open_listen_stream(self) -> None:
        """Open the GET stream for unsolicited server messages; raises on 405."""
        if self._get_stream is not None:
            return
        headers = {"Accept": "text/event-stream", **self.base_headers()}
        if self.trace:
            trace_info(f"GET {self.url} (listening stream)")
        resp = self._request("GET", headers, None, read_timeout=None)
        try:
            if self.trace:
                trace_info(f"HTTP {resp.status} {resp.headers.get('Content-Type', '')}".rstrip())
            self._check_status(resp)
            if "text/event-stream" not in resp.headers.get("Content-Type", ""):
                raise TransportError("GET did not return an SSE stream")
        except Exception:
            resp.release_conn()
            raise
        self._get_stream = self._start_stream(resp, "GET", None)

    def close_listen_stream(self) -> None:
        if self._get_stream is not None:
            self._abandon(self._get_stream)
            self._get_stream = None

    @property
    def listening(self) -> bool:
        return self._get_stream is not None

    # ── lifecycle ──────────────────────────────────────────────────────

    def close(self) -> None:
        for s in list(self._streams):
            self._abandon(s)
        self._get_stream = None
        # Per spec (2025-03-26 to 2025-11-25), DELETE ends the session.
        if self._session_id and self.wire.sessions:
            try:
                resp = self._request("DELETE", self.base_headers(), None)
                resp.read()
                resp.release_conn()
            except Exception:
                pass  # best-effort; we're closing anyway
        self._session_id = None
        self._inbox.clear()
        self._pool.clear()

    def reconnect(self) -> None:
        eprint(bold_err("Starting a new HTTP session..."))
        self.close()
