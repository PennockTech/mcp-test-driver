# Copyright 2026 Phil Pennock — see LICENSE file.

"""Entry point for mcp-test-driver."""

from __future__ import annotations

import sys


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

    if target.startswith("http://") or target.startswith("https://"):
        _run_http(target)
    else:
        _run_stdio(sys.argv[1:])


def _run_stdio(command: list[str]) -> None:
    from .protocol import McpSession
    from .repl import Repl
    from .transport import StdioTransport

    transport = StdioTransport(command)
    try:
        session = McpSession(transport)
        session.initialize()
        repl = Repl(session)
        repl.run()
    finally:
        transport.close()


def _run_http(url: str) -> None:
    from .protocol import McpSession
    from .repl import Repl
    from .transport import HttpTransport

    transport = HttpTransport(url)
    try:
        session = McpSession(transport)
        session.initialize()
        repl = Repl(session)
        repl.run()
    finally:
        transport.close()
