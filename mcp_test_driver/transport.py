# Copyright 2026 Phil Pennock — see LICENSE file.

"""Transport layer for MCP communication.

Defines the Transport protocol and provides StdioTransport for
subprocess-based MCP servers.  HttpTransport is added in Stage 2.
"""

from __future__ import annotations

import json
import subprocess
from typing import Protocol

from .color import bold_err, cyan, eprint, yellow


class Transport(Protocol):
    """Abstract transport for MCP JSON-RPC communication."""

    trace: bool

    def request(self, obj: dict[str, object]) -> dict[str, object] | None:
        """Send a JSON-RPC request and return the response."""
        ...

    def notify(self, obj: dict[str, object]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        ...

    def close(self) -> None:
        """Shut down the transport."""
        ...

    def reconnect(self) -> None:
        """Tear down and re-establish the connection."""
        ...


def _frame(obj: dict[str, object]) -> bytes:
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

    def _send(self, obj: dict[str, object]) -> None:
        assert self._proc is not None
        assert self._proc.stdin is not None
        if self.trace:
            eprint(cyan(f">>> {json.dumps(obj)}"))
        self._proc.stdin.write(_frame(obj))
        self._proc.stdin.flush()

    def _recv(self) -> dict[str, object] | None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        line = self._proc.stdout.readline()
        if not line:
            return None
        obj = json.loads(line)
        if self.trace:
            eprint(yellow(f"<<< {json.dumps(obj)}"))
        return obj  # type: ignore[no-any-return]

    def request(self, obj: dict[str, object]) -> dict[str, object] | None:
        self._send(obj)
        return self._recv()

    def notify(self, obj: dict[str, object]) -> None:
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
