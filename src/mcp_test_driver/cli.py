# Copyright 2026 Phil Pennock — see LICENSE file.

"""Entry point for mcp-test-driver."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import versions

if TYPE_CHECKING:
    from .transport import Transport

DESCRIPTION = """\
Interactive REPL and diagnostic driver for MCP servers.

The target is either a command to run as a stdio server, or an http(s) URL.
Options must come before the target; everything after it is passed to the
server command unchanged."""

EPILOG = f"""\
examples:
  mcp-test-driver aifr mcp
  mcp-test-driver https://unicode.mcp.pennock.tech/mcp
  mcp-test-driver --no-trace aifr mcp
  mcp-test-driver --roots=./myproject https://example.com/mcp
  mcp-test-driver --protocol-version 2025-06-18 my-server --stdio
  mcp-test-driver --header 'X-Api-Key: abc' https://example.com/mcp
  TOKEN=... mcp-test-driver --bearer-env TOKEN https://example.com/mcp

known protocol revisions: {", ".join(versions.KNOWN_VERSIONS)}
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcp-test-driver",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    from . import __version__

    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    g = p.add_argument_group("output")
    g.add_argument("--trace", dest="trace", action="store_true", default=True,
                   help="show JSON-RPC traffic (default)")
    g.add_argument("--no-trace", dest="trace", action="store_false",
                   help="hide JSON-RPC traffic at start-up (toggle with /trace)")
    g.add_argument("-q", "--quiet", action="store_true",
                   help="skip the start-up connection report")

    g = p.add_argument_group("protocol")
    g.add_argument("--protocol-version", metavar="REV",
                   help=f"revision to offer/request (default: {versions.LATEST_HANDSHAKE} "
                   f"for initialize, {versions.LATEST_MODERN} for modern requests)")
    g.add_argument("--timeout", type=float, default=None, metavar="SECS",
                   help="how long to wait for a reply (default 120)")
    g.add_argument("--roots", type=Path, metavar="PATH",
                   help="advertise a filesystem root (bare --roots: the current directory)")
    g.add_argument("--elicitation", nargs="?", const="form,url", metavar="MODES",
                   help="answer elicitation requests interactively; MODES is form, url "
                   "or form,url (the default)")
    g.add_argument("--sampling", action="store_true",
                   help="answer sampling requests yourself (you play the LLM)")
    g.add_argument("--progress", action="store_true",
                   help="attach progress tokens to calls")

    g = p.add_argument_group("transport")
    g.add_argument("--transport", choices=("auto", "stdio", "http"), default="auto",
                   help="force a transport instead of guessing from the target")
    g.add_argument("--header", action="append", default=[], metavar="'NAME: VALUE'",
                   help="extra HTTP header (repeatable)")
    g.add_argument("--bearer-env", metavar="VAR",
                   help="send 'Authorization: Bearer $VAR' (keeps the token out of argv)")
    g.add_argument("--insecure", action="store_true",
                   help="do not verify TLS certificates (testing only)")

    p.add_argument("target", nargs=argparse.REMAINDER,
                   help="server command and its arguments, or an http(s) URL")
    return p


def _normalise_argv(argv: list[str]) -> list[str]:
    """`--roots` alone means the current directory.

    It cannot take a separate value word, or `--roots aifr mcp` would treat
    the server command as the path; only `--roots=PATH` sets one.
    """
    out = []
    for i, a in enumerate(argv):
        if a == "--roots":
            out.append(f"--roots={Path.cwd()}")
        elif not a.startswith("-"):
            out.extend(argv[i:])
            break
        else:
            out.append(a)
    return out


def parse_headers(specs: list[str]) -> dict[str, str]:
    from .transport import check_header

    headers: dict[str, str] = {}
    for spec in specs:
        name, sep, value = spec.partition(":")
        if not sep:
            raise ValueError(f"--header needs 'Name: value', got {spec!r}")
        name, value = name.strip(), value.strip()
        check_header(name, value)
        headers[name] = value
    return headers


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(_normalise_argv(sys.argv[1:] if argv is None else argv))
    if not args.target:
        parser.print_usage()
        sys.exit(1)
    if args.protocol_version and not versions.is_known(args.protocol_version):
        print(f"Note: {args.protocol_version} is not a revision this client knows; "
              "offering it anyway.", file=sys.stderr)
    try:
        transport = make_transport(args)
    except (ValueError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # TransportError, urllib3 setup errors
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        run_session(transport, args)
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    finally:
        transport.close()


def make_transport(args: argparse.Namespace) -> Transport:
    from .transport import DEFAULT_TIMEOUT, HttpTransport, StdioTransport

    target: list[str] = args.target
    timeout = args.timeout or DEFAULT_TIMEOUT
    is_url = target[0].startswith(("http://", "https://"))
    kind = args.transport if args.transport != "auto" else ("http" if is_url else "stdio")
    if kind == "http":
        if len(target) > 1:
            raise ValueError("an HTTP target takes no further arguments")
        headers = parse_headers(args.header)
        if args.bearer_env:
            token = os.environ.get(args.bearer_env)
            if not token:
                raise ValueError(f"environment variable {args.bearer_env} is empty or unset")
            headers["Authorization"] = f"Bearer {token}"
        transport: Transport = HttpTransport(
            target[0], verify_tls=not args.insecure, timeout=timeout, extra_headers=headers
        )
    else:
        if args.header or args.bearer_env:
            print("Note: --header/--bearer-env have no effect on stdio; pass credentials "
                  "to the server via its environment.", file=sys.stderr)
        transport = StdioTransport(target, timeout=timeout)
    transport.trace = args.trace
    return transport


def run_session(transport: Transport, args: argparse.Namespace) -> None:
    from .negotiate import ConnectOptions, connect
    from .protocol import McpError, McpSession
    from .repl import Repl, print_findings
    from .transport import TransportError

    session = McpSession(transport)
    if args.roots is not None:
        try:
            session.enable_roots(args.roots)
        except (OSError, ValueError) as e:
            print(f"Error: cannot use --roots {args.roots}: {e}", file=sys.stderr)
            sys.exit(1)
    if args.elicitation or args.sampling:
        from .completion import suspend_completion
        from .interactive import ConsolePrompter

        prompter = ConsolePrompter(suspend_completion)
        if args.elicitation:
            modes = frozenset(m.strip() for m in args.elicitation.split(","))
            if not modes <= {"form", "url"}:
                print("Error: --elicitation modes are form and/or url", file=sys.stderr)
                sys.exit(1)
            session.enable_elicitation(prompter, modes)
        if args.sampling:
            session.enable_sampling(prompter)
    session.progress = args.progress
    try:
        connect(session, ConnectOptions(era="legacy", version=args.protocol_version))
    except TransportError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except McpError as e:
        print(f"MCP error: {e}", file=sys.stderr)
        sys.exit(1)
    except ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)
    repl = Repl(session)
    if not args.quiet:
        from .report import print_report

        print_report(session, repl.cache)
    else:
        print_findings(session.findings.take_unshown())
    repl.run()
