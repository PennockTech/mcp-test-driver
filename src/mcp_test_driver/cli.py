# Copyright 2026 Phil Pennock — see LICENSE file.

"""Entry point for mcp-test-driver."""

from __future__ import annotations

import sys
from typing import Any


USAGE = """\
Usage: mcp-test-driver <command> [args...]    stdio transport
       mcp-test-driver <url>                  HTTP transport

Examples:
  mcp-test-driver aifr mcp
  mcp-test-driver character agent mcp
  mcp-test-driver https://unicode.mcp.pennock.tech/mcp

Options:
  -h, --help    Show this help message
"""


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    target = sys.argv[1]

    try:
        if target.startswith("http://") or target.startswith("https://"):
            _run_http(target)
        else:
            _run_stdio(sys.argv[1:])
    except KeyboardInterrupt:
        print()
        sys.exit(130)


def _run_stdio(command: list[str]) -> None:
    from .transport import StdioTransport, TransportError

    try:
        transport = StdioTransport(command)
    except TransportError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        _run_session(transport)
    finally:
        transport.close()


def _run_http(url: str) -> None:
    from .transport import HttpTransport

    try:
        transport = HttpTransport(url)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        _run_session(transport)
    finally:
        transport.close()


def _run_session(transport: Any) -> None:
    from .protocol import McpError, McpSession
    from .repl import Repl
    from .transport import TransportError

    try:
        session = McpSession(transport)
        session.initialize()
        repl = Repl(session)
        repl.run()
    except TransportError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except McpError as e:
        print(f"MCP error: {e}", file=sys.stderr)
        sys.exit(1)
    except ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)
