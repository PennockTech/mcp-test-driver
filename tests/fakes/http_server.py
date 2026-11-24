"""Fake MCP server over Streamable HTTP, driven by the same engine as stdio.

Extra scenario keys:
    http_mode        "json" (default) | "sse"      how request replies are sent
    session_id       issue this Mcp-Session-Id on initialize and require it after
    status_for       {method: [status, body-json-or-null, {headers}]} canned replies
    sse_prelude      raw SSE text sent before the first event (priming, comments)
    resumable        give SSE events ids ("<stream>-<n>") so streams can resume
    drop_after       close a POST stream after this many events, without its
                     response; GET with Last-Event-ID continues it
    sse_retry_ms     send this SSE retry field before closing a dropped stream
    get_events       accept GET: send these messages, then keep the stream open
    legacy_sse       act as a 2024-11-05 HTTP+SSE server: GET /mcp streams, the
                     first event names the POST endpoint; POST /mcp gives 405
    endpoint         the endpoint event's data (default "/messages?sid=1")
    require_bearer   reject requests without "Authorization: Bearer <this>" (401)
    www_authenticate the WWW-Authenticate header sent with that 401
    documents        {path: json} served on GET (metadata, server cards)
    oauth            run a fake authorization server (see auth_server.py) and
                     require its tokens on the MCP endpoint
"""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .auth_server import FakeAuth
from .engine import FakeMcp


class FakeHttpServer:
    def __init__(self, scenario: dict[str, Any]) -> None:
        self.scenario = scenario
        self.mcp = FakeMcp(scenario)
        self.responses: queue.Queue[Any] = queue.Queue()
        # (method, path, headers, body) for every HTTP request received.
        self.requests: list[tuple[str, str, dict[str, str], Any]] = []
        self.lock = threading.Lock()
        self.cancelled: dict[Any, threading.Event] = {}
        self.parked: dict[str, Any] = {}
        self.event_counts: dict[str, int] = {}
        self.streams_opened = 0
        self.stopping = threading.Event()
        self.legacy_out: queue.Queue[Any] = queue.Queue()
        handler = _make_handler(self)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.auth = FakeAuth(scenario["oauth"], self.origin) if "oauth" in scenario else None

    @property
    def origin(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def url(self) -> str:
        return f"{self.origin}/mcp"

    def start(self) -> FakeHttpServer:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stopping.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def headers_for(self, method: str) -> list[dict[str, str]]:
        return [h for (m, _, h, b) in self.requests if m == "POST" and isinstance(b, dict) and b.get("method") == method]


def _make_handler(server: FakeHttpServer) -> type[BaseHTTPRequestHandler]:
    s = server.scenario

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def _send(self, status: int, body: bytes = b"", ctype: str = "application/json",
                  headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            if body:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_DELETE(self) -> None:  # noqa: N802
            with server.lock:
                server.requests.append(("DELETE", self.path, dict(self.headers), None))
            self._send(200)

        def _sse_start(self, extra: dict[str, str] | None = None) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def _event(self, obj: Any, key: str) -> None:
            head = ""
            if s.get("resumable"):
                with server.lock:
                    n = server.event_counts[key] = server.event_counts.get(key, 0) + 1
                head = f"id: {key}-{n}\n"
            self.wfile.write(f"{head}event: message\ndata: {json.dumps(obj)}\n\n".encode())
            self.wfile.flush()

        def _pump(self, gen: Any, key: str, drop_after: int | None = None) -> None:
            sent = 0
            to_send = None
            while True:
                try:
                    action, arg = gen.send(to_send)
                except StopIteration:
                    return
                to_send = None
                if action == "send":
                    self._event(arg, key)
                    sent += 1
                    if drop_after is not None and sent >= drop_after:
                        server.parked[key] = gen
                        if s.get("sse_retry_ms") is not None:
                            self.wfile.write(f"retry: {s['sse_retry_ms']}\n\n".encode())
                            self.wfile.flush()
                        return
                elif action == "await":
                    to_send = server.responses.get(timeout=10)
                elif action == "hold":
                    if server.cancelled.setdefault(arg, threading.Event()).wait(timeout=30):
                        self._event(server.mcp.listen_closed(arg), key)

        def _unauthorized(self, body: Any = None) -> bool:
            if server.auth is not None:
                denied = server.auth.check(dict(self.headers), body)
                if denied:
                    status, challenge = denied
                    self._send(status, b'{"error":"unauthorized"}', headers={"WWW-Authenticate": challenge})
                    return True
                return False
            token = s.get("require_bearer")
            if token and self.headers.get("Authorization") != f"Bearer {token}":
                headers = {"WWW-Authenticate": s["www_authenticate"]} if s.get("www_authenticate") else {}
                self._send(401, b'{"error":"unauthorized"}', headers=headers)
                return True
            return False

        def _legacy_stream(self) -> None:
            self._sse_start()
            endpoint = s.get("endpoint", "/messages?sid=1")
            self.wfile.write(f"event: endpoint\ndata: {endpoint}\n\n".encode())
            self.wfile.flush()
            while not server.stopping.is_set():
                try:
                    msg = server.legacy_out.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    self.wfile.write(f"event: message\ndata: {json.dumps(msg)}\n\n".encode())
                    self.wfile.flush()
                except OSError:
                    break
            self.close_connection = True

        def _legacy_post(self, msg: Any) -> None:
            def work() -> None:
                gen = server.mcp.handle(msg)
                to_send = None
                while True:
                    try:
                        action, arg = gen.send(to_send)
                    except StopIteration:
                        return
                    to_send = None
                    if action == "send":
                        server.legacy_out.put(arg)
                    elif action == "await":
                        to_send = server.responses.get(timeout=10)

            if isinstance(msg, dict) and "method" not in msg:
                server.responses.put(msg)
            else:
                threading.Thread(target=work, daemon=True).start()
            self._send(202, b"Accepted", ctype="text/plain")

        def do_GET(self) -> None:  # noqa: N802
            with server.lock:
                server.requests.append(("GET", self.path, dict(self.headers), None))
            path, _, query = self.path.partition("?")
            if server.auth is not None:
                got = server.auth.get(path, query)
                if got is not None:
                    status, headers, body = got
                    self.send_response(status)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            if path in s.get("documents", {}):
                self._send(200, json.dumps(s["documents"][path]).encode())
                return
            if path != "/mcp":
                self._send(404)
                return
            if self._unauthorized():
                return
            if s.get("legacy_sse"):
                self._legacy_stream()
                return
            last = self.headers.get("Last-Event-ID")
            if last:
                key = last.rsplit("-", 1)[0]
                gen = server.parked.pop(key, None)
                if gen is None:
                    self._send(404)
                    return
                self._sse_start()
                self._pump(gen, key)
                self.close_connection = True
                return
            if s.get("get_events") is None:
                self._send(405)
                return
            self._sse_start()
            for ev in s["get_events"]:
                self._event(ev, "get")
            # Keep the stream open until the client goes away.
            while not server.stopping.wait(0.2):
                try:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                except OSError:
                    break
            self.close_connection = True

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            if server.auth is not None and self.path.split("?", 1)[0] != "/mcp":
                got = server.auth.post(self.path.split("?", 1)[0], raw, dict(self.headers))
                if got is None:
                    self._send(404)
                    return
                status, headers, body = got
                self._send(status, body, ctype=headers.get("Content-Type", "application/json"))
                return
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"unparseable": raw.decode(errors="replace")}
            with server.lock:
                server.requests.append(("POST", self.path, dict(self.headers), msg))
            method = msg.get("method") if isinstance(msg, dict) else None
            if self._unauthorized(msg):
                return
            if s.get("legacy_sse"):
                if self.path.split("?", 1)[0] == "/mcp":
                    self._send(405)
                else:
                    if isinstance(msg, dict) and "method" not in msg:
                        server.mcp.log(msg)
                    self._legacy_post(msg)
                return

            canned = s.get("status_for", {}).get(method)
            if canned:
                status, body, headers = canned
                self._send(status, json.dumps(body).encode() if body is not None else b"", headers=headers)
                return

            sid = s.get("session_id")
            if sid and method != "initialize" and self.headers.get("Mcp-Session-Id") != sid:
                self._send(400, json.dumps({"jsonrpc": "2.0", "error": {"code": -32600, "message": "missing session"}}).encode())
                return

            if method == "notifications/cancelled":
                rid = (msg.get("params") or {}).get("requestId")
                server.cancelled.setdefault(rid, threading.Event()).set()
            if isinstance(msg, dict) and "method" not in msg and ("result" in msg or "error" in msg):
                server.mcp.log(msg)
                server.responses.put(msg)
                self._send(202)
                return

            gen = server.mcp.handle(msg)
            is_request = isinstance(msg, dict) and "id" in msg and method is not None
            extra = {"Mcp-Session-Id": sid} if sid and method == "initialize" else {}
            if not is_request:
                for _ in gen:
                    pass
                self._send(202, headers=extra)
                return
            if s.get("http_mode", "json") == "json":
                out = [arg for action, arg in gen if action == "send"]
                body = out[0] if len(out) == 1 else out
                self._send(200, json.dumps(body).encode() if out else b"", headers=extra)
                return
            self._sse_start(extra)
            if s.get("sse_prelude"):
                self.wfile.write(s["sse_prelude"].encode())
                self.wfile.flush()
            with server.lock:
                server.streams_opened += 1
                key = f"s{server.streams_opened}"
            drop = s.get("drop_after") if method not in ("initialize", "server/discover") else None
            self._pump(gen, key, drop_after=drop)
            self.close_connection = True

    return Handler
