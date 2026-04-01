# Copyright 2026 Phil Pennock — see LICENSE file.

"""Entry point for mcp-test-driver."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


USAGE = """\
Usage: mcp-test-driver [options] <command> [args...]    stdio transport
       mcp-test-driver [options] <url>                  HTTP transport

Examples:
  mcp-test-driver aifr mcp
  mcp-test-driver character agent mcp
  mcp-test-driver https://unicode.mcp.pennock.tech/mcp
  mcp-test-driver --no-trace aifr mcp
  mcp-test-driver --roots ./myproject https://example.com/mcp

Options:
  -h, --help         Show this help message
  --trace            Enable protocol tracing (default)
  --no-trace         Disable protocol tracing at startup
  --roots            Advertise cwd as filesystem root to the server
  --roots=<path>     Advertise a specific path as filesystem root
"""


def main() -> None:
    args = sys.argv[1:]
    trace = True
    roots_path: Path | None = None

    # Extract our flags before the command/URL.
    while args and args[0].startswith("-"):
        flag = args.pop(0)
        if flag in ("-h", "--help"):
            print(USAGE)
            sys.exit(0)
        elif flag == "--trace":
            trace = True
        elif flag == "--no-trace":
            trace = False
        elif flag == "--roots":
            # --roots alone uses cwd.  --roots=<path> uses the given path.
            roots_path = Path.cwd()
        elif flag.startswith("--roots="):
            roots_path = Path(flag.split("=", 1)[1])
        else:
            print(f"Unknown option: {flag}", file=sys.stderr)
            print(USAGE, file=sys.stderr)
            sys.exit(1)

    if not args:
        print(USAGE)
        sys.exit(1)

    target = args[0]

    try:
        if target.startswith("http://") or target.startswith("https://"):
            _run_http(target, trace=trace, roots_path=roots_path)
        else:
            _run_stdio(args, trace=trace, roots_path=roots_path)
    except KeyboardInterrupt:
        print()
        sys.exit(130)


def _run_stdio(
    command: list[str],
    *,
    trace: bool = True,
    roots_path: Path | None = None,
) -> None:
    from .transport import StdioTransport, TransportError

    try:
        transport = StdioTransport(command)
    except TransportError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    transport.trace = trace
    try:
        _run_session(transport, roots_path=roots_path)
    finally:
        transport.close()


def _run_http(
    url: str,
    *,
    trace: bool = True,
    roots_path: Path | None = None,
) -> None:
    from .transport import HttpTransport

    try:
        transport = HttpTransport(url)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    transport.trace = trace
    try:
        _run_session(transport, roots_path=roots_path)
    finally:
        transport.close()


def _run_session(transport: Any, *, roots_path: Path | None = None) -> None:
    from .protocol import McpError, McpSession
    from .repl import Repl
    from .transport import TransportError

    try:
        session = McpSession(transport)
        if roots_path is not None:
            session.enable_roots(roots_path)
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
