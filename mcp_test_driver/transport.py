# Copyright 2026 Phil Pennock — see LICENSE file.

"""Transport layer for MCP communication.

Defines the Transport protocol and provides StdioTransport for
subprocess-based MCP servers and HttpTransport for HTTP endpoints
using the MCP Streamable HTTP transport.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Protocol

from .color import bold_err, cyan, eprint, yellow


class Transport(Protocol):
    """Abstract transport for MCP JSON-RPC communication."""

    trace: bool

    def request(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        """Send a JSON-RPC request and return the response."""
        ...

    def notify(self, obj: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        ...

    def close(self) -> None:
        """Shut down the transport."""
        ...

    def reconnect(self) -> None:
        """Tear down and re-establish the connection."""
        ...


def _frame(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode() + b"\n"


class StdioTransport:
    """MCP transport over subprocess stdin/stdout."""

    def __init__(self, command: list[str]) -> None:
        self._command = command
        self.trace = True
        self._proc: subprocess.Popen[bytes] | None = None
        self._launch()

    def _launch(self) -> None:
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def _send(self, obj: dict[str, Any]) -> None:
        assert self._proc is not None
        assert self._proc.stdin is not None
        if self.trace:
            eprint(cyan(f">>> {json.dumps(obj)}"))
        self._proc.stdin.write(_frame(obj))
        self._proc.stdin.flush()

    def _recv(self) -> dict[str, Any] | None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        line = self._proc.stdout.readline()
        if not line:
            return None
        obj = json.loads(line)
        if self.trace:
            eprint(yellow(f"<<< {json.dumps(obj)}"))
        return obj  # type: ignore[no-any-return]

    def request(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        self._send(obj)
        return self._recv()

    def notify(self, obj: dict[str, Any]) -> None:
        self._send(obj)

    def close(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        assert proc.stdin is not None
        proc.stdin.close()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def reconnect(self) -> None:
        eprint(bold_err("Reconnecting..."))
        self.close()
        self._launch()
        eprint(bold_err("Reconnected."))

    def __del__(self) -> None:
        self.close()


class HttpTransport:
    """MCP transport over HTTP using the Streamable HTTP transport.

    Sends JSON-RPC messages as POST requests.  Handles responses as
    either application/json (direct) or text/event-stream (SSE).
    Tracks the Mcp-Session-Id header across requests.
    """

    def __init__(self, url: str) -> None:
        import urllib3

        self._url = url
        self.trace = True
        self._session_id: str | None = None
        self._pool = urllib3.PoolManager()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        body = json.dumps(obj, separators=(",", ":")).encode()
        if self.trace:
            eprint(cyan(f">>> {json.dumps(obj)}"))

        resp = self._pool.request(
            "POST",
            self._url,
            body=body,
            headers=self._headers(),
            preload_content=False,
        )

        # Track session ID from server
        new_session_id = resp.headers.get("Mcp-Session-Id")
        if new_session_id:
            self._session_id = new_session_id

        content_type = resp.headers.get("Content-Type", "")

        if "text/event-stream" in content_type:
            result = self._parse_sse(resp)
        elif "application/json" in content_type:
            data = resp.read()
            result = json.loads(data)
        elif resp.status == 202:
            # Accepted — no response body (for notifications)
            resp.read()
            return None
        else:
            data = resp.read()
            raise ValueError(
                f"Unexpected Content-Type {content_type!r} "
                f"(status {resp.status}): {data[:200]}"
            )

        resp.release_conn()

        if self.trace and result is not None:
            eprint(yellow(f"<<< {json.dumps(result)}"))
        return result

    def _parse_sse(self, resp: Any) -> dict[str, Any] | None:
        """Parse a Server-Sent Events stream, returning the last JSON-RPC message."""
        data_lines: list[str] = []
        last_message: dict[str, Any] | None = None

        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

            if line.startswith("data: "):
                data_lines.append(line[6:])
            elif line == "" and data_lines:
                # End of event — parse accumulated data
                payload = "\n".join(data_lines)
                data_lines.clear()
                try:
                    last_message = json.loads(payload)
                except json.JSONDecodeError:
                    pass
            # Ignore event:, id:, retry:, and comment lines

        # Handle trailing data without final blank line
        if data_lines:
            payload = "\n".join(data_lines)
            try:
                last_message = json.loads(payload)
            except json.JSONDecodeError:
                pass

        return last_message

    def request(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        return self._post(obj)

    def notify(self, obj: dict[str, Any]) -> None:
        self._post(obj)

    def close(self) -> None:
        self._pool.clear()

    def reconnect(self) -> None:
        eprint(bold_err("Reconnecting (new HTTP session)..."))
        self._session_id = None
        self._pool.clear()
        eprint(bold_err("Reconnected."))
