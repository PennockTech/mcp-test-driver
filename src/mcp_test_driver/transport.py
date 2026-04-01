# Copyright 2026 Phil Pennock — see LICENSE file.

"""Transport layer for MCP communication.

Defines the Transport protocol and provides StdioTransport for
subprocess-based MCP servers and HttpTransport for HTTP endpoints
using the MCP Streamable HTTP transport.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Protocol

from .color import bold_err, cyan, eprint, red, yellow

# Size and timeout limits.
MAX_LINE_BYTES = 16 * 1024 * 1024  # 16 MiB per JSON-RPC message
MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # 16 MiB for HTTP response bodies
HTTP_CONNECT_TIMEOUT = 10.0  # seconds to establish TCP+TLS
HTTP_READ_TIMEOUT = 60.0  # seconds to wait for response data
_MAX_ID_SKIP = 32  # max server notifications to skip while awaiting a response

# Characters that are unsafe for terminal display (control chars except
# common whitespace).  Used to sanitize server-supplied strings.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x9b]")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b.")


def sanitize(s: str) -> str:
    """Remove ANSI escape sequences and control characters from a string.

    Prevents terminal manipulation via malicious server data.
    """
    s = _ANSI_ESCAPE_RE.sub("", s)
    return _CONTROL_RE.sub("", s)


class TransportError(Exception):
    """Raised when a transport-level error occurs."""


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
        try:
            self._proc = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
        except FileNotFoundError:
            raise TransportError(f"Command not found: {self._command[0]}") from None
        except PermissionError:
            raise TransportError(f"Permission denied: {self._command[0]}") from None
        except OSError as e:
            raise TransportError(f"Cannot start {self._command[0]}: {e}") from None

    def _send(self, obj: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise TransportError("Not connected")
        if self.trace:
            eprint(cyan(f">>> {json.dumps(obj)}"))
        try:
            self._proc.stdin.write(_frame(obj))
            self._proc.stdin.flush()
        except BrokenPipeError:
            raise TransportError("Server process has exited") from None
        except OSError as e:
            raise TransportError(f"Write error: {e}") from None

    def _recv(self) -> dict[str, Any] | None:
        if self._proc is None or self._proc.stdout is None:
            raise TransportError("Not connected")
        try:
            line = self._proc.stdout.readline(MAX_LINE_BYTES)
        except OSError as e:
            raise TransportError(f"Read error: {e}") from None
        if not line:
            return None
        if len(line) >= MAX_LINE_BYTES and not line.endswith(b"\n"):
            eprint(
                red(f"Server sent line exceeding {MAX_LINE_BYTES} bytes; discarding")
            )
            # Drain the rest of the oversized line
            while True:
                chunk = self._proc.stdout.readline(MAX_LINE_BYTES)
                if not chunk or chunk.endswith(b"\n"):
                    break
            return None
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            eprint(red(f"Malformed JSON from server: {e}"))
            eprint(red(f"  Raw line: {line[:200]!r}"))
            return None
        if self.trace:
            eprint(yellow(f"<<< {json.dumps(obj)}"))
        return obj  # type: ignore[no-any-return]

    def request(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        self._send(obj)
        expected_id = obj.get("id")
        if expected_id is None:
            return self._recv()
        # Skip server notifications that arrive before our response.
        for _ in range(_MAX_ID_SKIP):
            resp = self._recv()
            if resp is None:
                return None
            if resp.get("id") is not None:
                return resp
            # This is a notification (no id) — log and skip
            method = resp.get("method", "?")
            if self.trace:
                eprint(yellow(f"  (skipped server notification: {method})"))
        return self._recv()

    def notify(self, obj: dict[str, Any]) -> None:
        self._send(obj)

    def close(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        if proc.stdin:
            try:
                proc.stdin.close()
            except OSError:
                pass
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

    Security: redirects are not followed (SSRF protection).  Response
    bodies are capped at MAX_RESPONSE_BYTES.  TLS certificates are
    verified by default.
    """

    def __init__(self, url: str, *, verify_tls: bool = True) -> None:
        import urllib3
        import urllib3.util

        self._url = url
        self.trace = True
        self._session_id: str | None = None
        self._verify_tls = verify_tls

        retries = urllib3.util.Retry(
            total=3,
            redirect=0,  # Do not follow redirects (SSRF protection)
        )
        timeout = urllib3.util.Timeout(
            connect=HTTP_CONNECT_TIMEOUT,
            read=HTTP_READ_TIMEOUT,
        )

        pool_kwargs: dict[str, Any] = {
            "retries": retries,
            "timeout": timeout,
        }
        if not verify_tls:
            pool_kwargs["cert_reqs"] = "CERT_NONE"
        self._pool = urllib3.PoolManager(**pool_kwargs)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        import urllib3.exceptions

        body = json.dumps(obj, separators=(",", ":")).encode()
        if self.trace:
            eprint(cyan(f">>> {json.dumps(obj)}"))

        try:
            resp = self._pool.request(
                "POST",
                self._url,
                body=body,
                headers=self._headers(),
                preload_content=False,
                redirect=False,
            )
        except urllib3.exceptions.MaxRetryError as e:
            reason = e.reason
            if isinstance(reason, urllib3.exceptions.SSLError):
                raise TransportError(
                    f"TLS/certificate error connecting to {self._url}: {reason}"
                ) from None
            raise TransportError(f"Connection failed: {reason}") from None
        except urllib3.exceptions.SSLError as e:
            raise TransportError(f"TLS/certificate error: {e}") from None
        except urllib3.exceptions.HTTPError as e:
            raise TransportError(f"HTTP error: {e}") from None

        try:
            # Reject redirects explicitly (defense in depth)
            if 300 <= resp.status < 400:
                location = resp.headers.get("Location", "(none)")
                resp.read()
                raise TransportError(
                    f"Server returned redirect {resp.status} to {location}; "
                    f"redirects are not followed for security"
                )

            # Validate status code
            if resp.status >= 400:
                body_preview = resp.read(1024)
                raise TransportError(
                    f"HTTP {resp.status} from server: {body_preview[:200]!r}"
                )

            # Track session ID from server.
            # Per MCP spec, IDs must be visible ASCII (0x21-0x7E).
            # Strip anything else to prevent header injection.
            new_session_id = resp.headers.get("Mcp-Session-Id")
            if new_session_id:
                self._session_id = re.sub(r"[^\x21-\x7e]", "", new_session_id)

            content_type = resp.headers.get("Content-Type", "")

            if "text/event-stream" in content_type:
                result = self._parse_sse(resp)
            elif "application/json" in content_type:
                data = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise TransportError(
                        f"Response body exceeds {MAX_RESPONSE_BYTES} bytes"
                    )
                try:
                    result = json.loads(data)
                except json.JSONDecodeError as e:
                    eprint(red(f"Malformed JSON from server: {e}"))
                    eprint(red(f"  Raw body: {data[:200]!r}"))
                    return None
            elif resp.status == 202:
                resp.read()
                return None
            else:
                data = resp.read(1024)
                raise TransportError(
                    f"Unexpected Content-Type {content_type!r} "
                    f"(status {resp.status}): {data[:200]}"
                )
        finally:
            resp.release_conn()

        if self.trace and result is not None:
            eprint(yellow(f"<<< {json.dumps(result)}"))
        return result

    def _parse_sse(self, resp: Any) -> dict[str, Any] | None:
        """Parse a Server-Sent Events stream, returning the last JSON-RPC message.

        Enforces a total byte limit to prevent memory exhaustion from
        malicious streams.
        """
        data_lines: list[str] = []
        last_message: dict[str, Any] | None = None
        total_bytes = 0

        for raw_line in resp:
            total_bytes += len(raw_line)
            if total_bytes > MAX_RESPONSE_BYTES:
                raise TransportError(f"SSE stream exceeds {MAX_RESPONSE_BYTES} bytes")

            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

            if line.startswith("data: "):
                data_lines.append(line[6:])
            elif line == "" and data_lines:
                payload = "\n".join(data_lines)
                data_lines.clear()
                try:
                    last_message = json.loads(payload)
                except json.JSONDecodeError:
                    pass

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
