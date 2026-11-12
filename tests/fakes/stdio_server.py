"""Fake MCP server over stdio.  Usage: python stdio_server.py SCENARIO.json"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import FakeMcp  # noqa: E402


def write(obj: object) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def run_actions(server: FakeMcp, gen) -> None:  # noqa: ANN001
    to_send = None
    while True:
        try:
            action, arg = gen.send(to_send)
        except StopIteration:
            return
        to_send = None
        if action == "send":
            write(arg)
        elif action == "await":
            to_send = await_response(server)
        elif action == "stderr":
            sys.stderr.write(arg + "\n")
            sys.stderr.flush()
        elif action == "raw":
            sys.stdout.write(arg + "\n")
            sys.stdout.flush()
        elif action == "sleep":
            time.sleep(arg)
        elif action == "exit":
            sys.stdout.flush()
            os._exit(arg)


def await_response(server: FakeMcp) -> object:
    """Read until the client answers; anything else is handled as usual."""
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        if isinstance(msg, dict) and "method" not in msg:
            server.log(msg)
            return msg
        run_actions(server, server.handle(msg))
    sys.exit(0)


def main() -> None:
    with open(sys.argv[1]) as f:
        server = FakeMcp(json.load(f))
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            server.log({"unparseable": line.rstrip("\n")})
            write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        run_actions(server, server.handle(msg))


if __name__ == "__main__":
    main()
