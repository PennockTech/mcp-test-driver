"""A fake HTTP proxy for tests: CONNECT tunnels and absolute-form forwarding.

Every target host is mapped to 127.0.0.1, so tests can use names such as
mcp.example.test that resolve *only* through this proxy: a request that
succeeds must have been proxied.  Bytes are relayed as they arrive, so SSE
streams work.

    proxy = FakeProxy(require_auth=("user", "pw")).start()
    proxy.url            "http://127.0.0.1:<port>"
    proxy.log            [(method, target, proxy-authorization or None)]
"""

from __future__ import annotations

import base64
import select
import socket
import threading
from urllib.parse import urlsplit


class FakeProxy:
    def __init__(self, *, require_auth: tuple[str, str] | None = None) -> None:
        self.require_auth = require_auth
        self.log: list[tuple[str, str, str | None]] = []
        self._sock = socket.create_server(("127.0.0.1", 0))
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._sock.getsockname()[1]}"

    def start(self) -> FakeProxy:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopping.set()
        self._sock.close()

    def targets(self) -> list[str]:
        return [target for _, target, _ in self.log]

    def _serve(self) -> None:
        while not self._stopping.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(65536)
                if not chunk:
                    return
                head += chunk
            head, rest = head.split(b"\r\n\r\n", 1)
            lines = head.decode("latin-1").split("\r\n")
            method, target, version = lines[0].split(" ", 2)
            headers = [line for line in lines[1:] if line]
            auth = next((h.split(":", 1)[1].strip() for h in headers
                         if h.lower().startswith("proxy-authorization:")), None)
            self.log.append((method, target, auth))
            if self.require_auth is not None:
                want = "Basic " + base64.b64encode(":".join(self.require_auth).encode()).decode()
                if auth != want:
                    client.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                                   b"Proxy-Authenticate: Basic realm=\"fake\"\r\nContent-Length: 0\r\n\r\n")
                    return
            if method == "CONNECT":
                _, port = target.rsplit(":", 1)
                upstream = socket.create_connection(("127.0.0.1", int(port)))
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
            else:
                parts = urlsplit(target)
                upstream = socket.create_connection(("127.0.0.1", parts.port or 80))
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                # One request per connection keeps this simple: the next request
                # comes in absolute form, so it must be seen (and logged) here.
                kept = [h for h in headers if not h.lower().startswith(
                    ("proxy-authorization:", "proxy-connection:", "connection:", "keep-alive:"))]
                kept.append("Connection: close")
                upstream.sendall(f"{method} {path} {version}\r\n".encode("latin-1")
                                 + "".join(h + "\r\n" for h in kept).encode("latin-1") + b"\r\n" + rest)
                _relay(client, upstream, close_response=True)
                return
            _relay(client, upstream)
        except OSError:
            pass
        finally:
            client.close()


def _relay(a: socket.socket, b: socket.socket, *, close_response: bool = False) -> None:
    """Copy bytes both ways until either side closes.

    With close_response, the response head from b gets "Connection: close",
    as a real proxy would say when it won't reuse the connection.
    """
    head = b"" if close_response else None
    try:
        while True:
            ready, _, _ = select.select([a, b], [], [], 30)
            if not ready:
                return
            for src in ready:
                data = src.recv(65536)
                if not data:
                    return
                if src is b and head is not None:
                    head += data
                    if b"\r\n\r\n" not in head:
                        continue
                    top, body = head.split(b"\r\n\r\n", 1)
                    lines = [ln for ln in top.split(b"\r\n") if not ln.lower().startswith(b"connection:")]
                    data = b"\r\n".join([*lines, b"Connection: close"]) + b"\r\n\r\n" + body
                    head = None
                (b if src is a else a).sendall(data)
    finally:
        b.close()
