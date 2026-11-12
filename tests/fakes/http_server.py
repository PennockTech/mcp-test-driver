"""Fake MCP server over Streamable HTTP, driven by the same engine as stdio.

Extra scenario keys:
    http_mode        "json" (default) | "sse"      how request replies are sent
    session_id       issue this Mcp-Session-Id on initialize and require it after
    status_for       {method: [status, body-json-or-null, {headers}]} canned replies
    sse_prelude      raw SSE text sent before the first event (priming, comments)
"""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .engine import FakeMcp


class FakeHttpServer:
    def __init__(self, scenario: dict[str, Any]) -> None:
        self.scenario = scenario
        self.mcp = FakeMcp(scenario)
        self.responses: queue.Queue[Any] = queue.Queue()
        # (method, path, headers, body) for every HTTP request received.
        self.requests: list[tuple[str, str, dict[str, str], Any]] = []
        self.lock = threading.Lock()
        handler = _make_handler(self)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/mcp"

    def start(self) -> FakeHttpServer:
        self.thread.start()
        return self

    def stop(self) -> None:
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

        def do_GET(self) -> None:  # noqa: N802
            with server.lock:
                server.requests.append(("GET", self.path, dict(self.headers), None))
            self._send(405)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"unparseable": raw.decode(errors="replace")}
            with server.lock:
                server.requests.append(("POST", self.path, dict(self.headers), msg))
            method = msg.get("method") if isinstance(msg, dict) else None

            canned = s.get("status_for", {}).get(method)
            if canned:
                status, body, headers = canned
                self._send(status, json.dumps(body).encode() if body is not None else b"", headers=headers)
                return

            sid = s.get("session_id")
            if sid and method != "initialize" and self.headers.get("Mcp-Session-Id") != sid:
                self._send(400, json.dumps({"jsonrpc": "2.0", "error": {"code": -32600, "message": "missing session"}}).encode())
                return

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
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            if s.get("sse_prelude"):
                self.wfile.write(s["sse_prelude"].encode())
                self.wfile.flush()
            to_send = None
            while True:
                try:
                    action, arg = gen.send(to_send)
                except StopIteration:
                    break
                to_send = None
                if action == "send":
                    self.wfile.write(f"event: message\ndata: {json.dumps(arg)}\n\n".encode())
                    self.wfile.flush()
                elif action == "await":
                    to_send = server.responses.get(timeout=10)
            self.close_connection = True

    return Handler
