# Copyright 2026 Phil Pennock — see LICENSE file.

"""MCP over a subprocess's stdin/stdout."""

from __future__ import annotations

import json
import os
import queue
import select
import subprocess
import threading
import time
from collections import deque
from collections.abc import Mapping
from typing import IO, Any

from ..color import bold_err, dim, eprint, red
from ..messages import Payload, expand
from ..sanitize import sanitize
from .base import (
    DEFAULT_TIMEOUT,
    MAX_LINE_BYTES,
    ConnectionClosed,
    ReceiveTimeout,
    TransportError,
    WireProfile,
    trace_info,
    trace_recv,
    trace_send,
)

# While blocked waiting for stdout we wake this often to show stderr lines,
# so they appear promptly but are printed by the main thread only.
_STDERR_FLUSH_INTERVAL = 0.2


def _frame(payload: Payload) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode() + b"\n"


def _stderr_reader(pipe: IO[bytes], out: queue.Queue[str]) -> None:
    try:
        for raw_line in pipe:
            out.put(raw_line.decode("utf-8", errors="replace").rstrip("\r\n"))
    except (OSError, ValueError):
        pass  # pipe closed


class StdioTransport:
    """MCP transport over subprocess stdin/stdout.

    Reads use select() on the raw stdout fd plus our own line buffer, so a
    timeout holds even if the server writes a partial line and stalls.
    """

    kind = "stdio"

    def __init__(self, command: list[str], *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._command = command
        self.timeout = timeout
        self.trace = True
        self.wire = WireProfile(sessions=False)
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_q: queue.Queue[str] = queue.Queue()
        self._buf = bytearray()
        self._discarding = False
        self._ready: deque[Any] = deque()
        # Counts batches received, for diagnostics.
        self.batches_received = 0
        self._launch()

    # ── lifecycle ──────────────────────────────────────────────────────

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
        self._buf.clear()
        self._discarding = False
        self._ready.clear()
        if self._proc.stderr is not None:
            self._stderr_thread = threading.Thread(
                target=_stderr_reader,
                args=(self._proc.stderr, self._stderr_q),
                daemon=True,
            )
            self._stderr_thread.start()

    @property
    def session_id(self) -> str | None:
        return None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def describe(self) -> str:
        return "stdio: " + " ".join(self._command)

    def returncode(self) -> int | None:
        return self._proc.poll() if self._proc else None

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
        # Spec shutdown sequence: close stdin, then SIGTERM, then SIGKILL.
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1)
            self._stderr_thread = None
        self.flush_stderr()

    def reconnect(self) -> None:
        eprint(bold_err("Restarting server process..."))
        self.close()
        self._launch()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # ── I/O ────────────────────────────────────────────────────────────

    def send(self, payload: Payload, *, headers: Mapping[str, str] | None = None) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise TransportError("Not connected")
        if self.trace:
            trace_send(payload)
        try:
            proc.stdin.write(_frame(payload))
            proc.stdin.flush()
        except BrokenPipeError:
            raise ConnectionClosed("Server process has exited") from None
        except OSError as e:
            raise TransportError(f"Write error: {e}") from None

    def send_bytes(self, data: bytes) -> None:
        """Write raw bytes plus a newline, for malformed-input testing."""
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise TransportError("Not connected")
        if self.trace:
            trace_info(f"raw write: {sanitize(data.decode('utf-8', errors='replace'))}")
        try:
            proc.stdin.write(data.rstrip(b"\n") + b"\n")
            proc.stdin.flush()
        except BrokenPipeError:
            raise ConnectionClosed("Server process has exited") from None

    def flush_stderr(self) -> None:
        while True:
            try:
                text = self._stderr_q.get_nowait()
            except queue.Empty:
                return
            text = sanitize(text)
            if text:
                eprint(dim(f"[server stderr] {text}"))

    def receive(self, timeout: float | None = None) -> Any:
        if timeout is None:
            timeout = self.timeout
        deadline = time.monotonic() + timeout
        while True:
            if self._ready:
                return self._ready.popleft()
            line = self._take_line()
            if line is not None:
                self._decode(line)
                continue
            self.flush_stderr()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReceiveTimeout(f"Server did not respond within {timeout:g}s")
            self._fill(min(remaining, _STDERR_FLUSH_INTERVAL))

    def poll(self) -> list[Any]:
        out: list[Any] = []
        while True:
            try:
                out.append(self.receive(timeout=0))
            except ReceiveTimeout:
                return out

    def _fd(self) -> int:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise TransportError("Not connected")
        return proc.stdout.fileno()

    def _fill(self, wait: float) -> None:
        fd = self._fd()
        try:
            ready, _, _ = select.select([fd], [], [], max(wait, 0))
        except (ValueError, OSError) as e:
            raise ConnectionClosed(f"Server stdout unavailable: {e}") from None
        if not ready:
            return
        try:
            chunk = os.read(fd, 65536)
        except OSError as e:
            raise TransportError(f"Read error: {e}") from None
        if not chunk:
            self.flush_stderr()
            if self._buf.strip() and not self._discarding:
                # A final message without its newline; still deliver it.
                line = bytes(self._buf)
                self._buf.clear()
                self._decode(line)
                return
            code = self.returncode()
            detail = f" (exit status {code})" if code is not None else ""
            raise ConnectionClosed(f"Server closed its stdout{detail}")
        self._buf += chunk

    def _take_line(self) -> bytes | None:
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                if len(self._buf) > MAX_LINE_BYTES:
                    if not self._discarding:
                        eprint(red(f"Server sent a line over {MAX_LINE_BYTES} bytes; discarding"))
                    self._discarding = True
                    self._buf.clear()
                return None
            if self._discarding or nl > MAX_LINE_BYTES:
                if not self._discarding:
                    eprint(red(f"Server sent a line over {MAX_LINE_BYTES} bytes; discarding"))
                self._discarding = False
                del self._buf[: nl + 1]
                continue
            line = bytes(self._buf[:nl])
            del self._buf[: nl + 1]
            if line.strip():
                return line

    def _decode(self, line: bytes) -> None:
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            eprint(red(f"Malformed JSON from server: {e}"))
            eprint(red(f"  Raw line: {sanitize(repr(line[:200]))}"))
            return
        if self.trace:
            trace_recv(obj)
        items, is_batch = expand(obj)
        if is_batch:
            self.batches_received += 1
        self._ready.extend(items)
