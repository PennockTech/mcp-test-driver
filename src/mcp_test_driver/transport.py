# Copyright 2026 Phil Pennock — see LICENSE file.

"""Transport layer for MCP communication.

Defines the Transport protocol and provides StdioTransport for
subprocess-based MCP servers and HttpTransport for HTTP endpoints
using the MCP Streamable HTTP transport.

Server-to-client requests (bidirectional MCP):

  MCP allows the server to send JSON-RPC requests to the client.
  Every message from the server is classified as one of:

    - Response (has "id", no "method") → returned to the caller
    - Notification (has "method", no "id") → displayed and skipped
    - Server request (has both "method" and "id") → dispatched to a
      handler in the HandlerRegistry, response sent back to the server

  StdioTransport handles all three cases in its read loop.
  HttpTransport currently does NOT support server-initiated requests
  because that requires a long-lived GET SSE stream (not yet implemented).
"""

from __future__ import annotations

import json
import re
import select
import subprocess
import threading
from typing import Any, Protocol

from .color import bold_err, cyan, dim, eprint, red, yellow

# Size and timeout limits.
MAX_LINE_BYTES = 16 * 1024 * 1024  # 16 MiB per JSON-RPC message
MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # 16 MiB for HTTP response bodies
HTTP_CONNECT_TIMEOUT = 10.0  # seconds to establish TCP+TLS
HTTP_READ_TIMEOUT = 60.0  # seconds to wait for response data
_MAX_ID_SKIP = 32  # max server notifications to skip while awaiting a response
STDIO_READ_TIMEOUT = 120.0  # seconds to wait for a stdio response line

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


class SessionExpiredError(TransportError):
    """Raised when the server returns 404, indicating the session has expired."""


class Transport(Protocol):
    """Abstract transport for MCP JSON-RPC communication."""

    trace: bool
    handler_registry: Any  # HandlerRegistry | None — avoids circular import

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


def _stderr_reader(pipe: Any) -> None:
    """Read stderr from a subprocess and display sanitized lines."""
    try:
        for raw_line in pipe:
            text = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            text = sanitize(text)
            if text:
                eprint(dim(f"[server stderr] {text}"))
    except (OSError, ValueError):
        pass  # pipe closed


class StdioTransport:
    """MCP transport over subprocess stdin/stdout."""

    def __init__(self, command: list[str]) -> None:
        self._command = command
        self.trace = True
        self.handler_registry: Any = None  # set by McpSession
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr_thread: threading.Thread | None = None
        self._launch()

    def _launch(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            raise TransportError(f"Command not found: {self._command[0]}") from None
        except PermissionError:
            raise TransportError(f"Permission denied: {self._command[0]}") from None
        except OSError as e:
            raise TransportError(f"Cannot start {self._command[0]}: {e}") from None
        # Read server stderr in a background thread so it doesn't block
        # and so we can sanitize it before display.
        if self._proc.stderr:
            self._stderr_thread = threading.Thread(
                target=_stderr_reader,
                args=(self._proc.stderr,),
                daemon=True,
            )
            self._stderr_thread.start()

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
            # Best-effort timeout: select detects if the OS-level buffer has
            # any data.  If the server sends partial data without a newline,
            # readline may still block — but this catches complete silence.
            try:
                ready, _, _ = select.select(
                    [self._proc.stdout], [], [], STDIO_READ_TIMEOUT
                )
            except (ValueError, OSError):
                ready = [True]  # fd closed or invalid; let readline detect it
            if not ready:
                raise TransportError(
                    f"Server did not respond within {STDIO_READ_TIMEOUT:.0f}s"
                )
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
        # Read messages from the server, handling notifications and
        # server-to-client requests until we get our response.
        for _ in range(_MAX_ID_SKIP):
            resp = self._recv()
            if resp is None:
                return None
            msg_method = resp.get("method")
            msg_id = resp.get("id")
            if msg_method is not None and msg_id is not None:
                # Server-to-client request — dispatch and respond.
                self._handle_server_request(msg_method, msg_id, resp.get("params", {}))
                continue
            if msg_method is not None and msg_id is None:
                # Notification — always display since we are a diagnostic tool.
                self._show_notification(resp)
                continue
            # Response to our request (has id, no method).
            return resp
        return self._recv()

    def _handle_server_request(
        self,
        method: str,
        req_id: Any,
        params: dict[str, Any],
    ) -> None:
        """Dispatch a server-to-client request to the handler registry.

        If no handler is registered, responds with "method not supported".
        The response is sent back to the server via stdin.
        """
        eprint(cyan(f"  <<< server request: {method} (id={req_id})"))
        registry = self.handler_registry
        if registry is not None and registry.has(method):
            try:
                result = registry.dispatch(method, params)
                self._send({"jsonrpc": "2.0", "id": req_id, "result": result})
            except Exception as e:
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": str(e)},
                    }
                )
        else:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not supported: {method}",
                    },
                }
            )

    @staticmethod
    def _show_notification(msg: dict[str, Any]) -> None:
        """Display a server notification to stderr."""
        method = msg.get("method", "?")
        params = msg.get("params")
        if params:
            eprint(cyan(f"  *** notification: {method} {json.dumps(params)}"))
        else:
            eprint(cyan(f"  *** notification: {method}"))

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
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1)
            self._stderr_thread = None

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
        # Server-to-client requests over HTTP require a long-lived GET SSE
        # stream, which is not yet implemented.  The registry is stored for
        # future use but dispatching is only active on StdioTransport today.
        self.handler_registry: Any = None
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
            if resp.status == 404 and self._session_id:
                resp.read()
                self._session_id = None
                raise SessionExpiredError(
                    "Session expired (HTTP 404) — re-initialize with /reconnect"
                )
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
                except json.JSONDecodeError as e:
                    eprint(red(f"Malformed JSON in SSE event: {e}"))
                    eprint(red(f"  Payload: {payload[:200]!r}"))

        # Handle trailing data without final blank line
        if data_lines:
            payload = "\n".join(data_lines)
            try:
                last_message = json.loads(payload)
            except json.JSONDecodeError as e:
                eprint(red(f"Malformed JSON in SSE event: {e}"))
                eprint(red(f"  Payload: {payload[:200]!r}"))

        return last_message

    def request(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        return self._post(obj)

    def notify(self, obj: dict[str, Any]) -> None:
        self._post(obj)

    def close(self) -> None:
        # Per MCP spec, send DELETE to terminate the session.
        if self._session_id:
            try:
                self._pool.request(
                    "DELETE",
                    self._url,
                    headers=self._headers(),
                    preload_content=True,
                    redirect=False,
                )
            except Exception:
                pass  # best-effort; we're closing anyway
            self._session_id = None
        self._pool.clear()

    def reconnect(self) -> None:
        eprint(bold_err("Reconnecting (new HTTP session)..."))
        self._session_id = None
        self._pool.clear()
        eprint(bold_err("Reconnected."))
