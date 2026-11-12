# Copyright 2026 Phil Pennock — see LICENSE file.

"""Raw protocol access: arbitrary requests, notifications and payloads.

These exist for negative and edge-case testing, so they deliberately do not
validate what the user sends.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..color import bold, dim, red
from ..messages import Kind, classify
from ..parse import parse_args
from ..sanitize import safe_json, sanitize
from ..transport import NoPendingMessages, ReceiveTimeout, check_header, redact_headers
from . import Arg, command

if TYPE_CHECKING:
    from ..repl import Repl

# How long /raw waits for replies it cannot tie to a request id.
_RAW_COLLECT_TIMEOUT = 2.0


def _params(text: str) -> dict[str, Any] | None:
    text = text.strip()
    return parse_args(text) if text else None


@command(
    "/send",
    group="Protocol",
    arg=Arg.METHOD,
    summary="Send any JSON-RPC request and show the response",
    usage=(
        "/send <method> [key=val ... | {json}]\n"
        "Framing (_meta, headers) follows the negotiated revision; use /raw to bypass it."
    ),
)
def cmd_send(repl: Repl, rest: str) -> None:
    parts = rest.strip().split(None, 1)
    if not parts:
        print(red("Usage: /send <method> [params]"))
        return
    resp = repl.session.send_request(parts[0], _params(parts[1] if len(parts) > 1 else ""))
    repl.show(resp)


@command(
    "/notify",
    group="Protocol",
    arg=Arg.METHOD,
    summary="Send any JSON-RPC notification",
    usage="/notify <method> [key=val ... | {json}]",
)
def cmd_notify(repl: Repl, rest: str) -> None:
    parts = rest.strip().split(None, 1)
    if not parts:
        print(red("Usage: /notify <method> [params]"))
        return
    repl.session.notify(parts[0], _params(parts[1] if len(parts) > 1 else ""))
    print(dim("Sent."))


def _ids(payload: Any) -> list[Any]:
    items = payload if isinstance(payload, list) else [payload]
    return [m["id"] for m in items if isinstance(m, dict) and "id" in m and "method" in m]


@command(
    "/raw",
    group="Protocol",
    summary="Send a payload exactly as typed (JSON, batch array, or invalid text)",
    usage=(
        "/raw <text>\n"
        "Valid JSON is sent as-is (no _meta added); anything else is sent as raw\n"
        "bytes, for testing the server's error handling.  Replies are shown raw."
    ),
)
def cmd_raw(repl: Repl, rest: str) -> None:
    text = rest.strip()
    if not text:
        print(red("Usage: /raw <json or text>"))
        return
    session = repl.session
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        send_bytes = getattr(session.transport, "send_bytes", None)
        if send_bytes is None:
            print(red("This transport cannot send non-JSON payloads."))
            return
        print(dim("(not valid JSON; sending raw bytes)"))
        send_bytes(text.encode())
        expected: list[Any] = []
    else:
        session.reserve_ids(_ids(payload))
        session.send_raw(payload)
        expected = _ids(payload)
    _collect(repl, expected)


def _collect(repl: Repl, expected: list[Any]) -> None:
    """Show replies: wait for each expected id, then whatever else is pending."""
    session = repl.session
    pending = list(expected)
    while True:
        # Expected replies get the normal timeout; after that (or for payloads
        # with no ids, such as malformed input) linger briefly for errors.
        timeout = None if pending else _RAW_COLLECT_TIMEOUT
        try:
            msg = session.transport.receive(timeout)
        except (ReceiveTimeout, NoPendingMessages):
            break
        if classify(msg) == Kind.RESPONSE and isinstance(msg, dict):
            rid = msg.get("id")
            print(bold(f"response id={sanitize(str(rid))}:"))
            print(safe_json(msg))
            if rid in pending:
                pending.remove(rid)
            if not pending and expected:
                break
            continue
        session.handle_incoming(msg)
    if pending:
        print(dim(f"(no response for id(s) {', '.join(map(str, pending))})"))


@command(
    "/meta",
    group="Protocol",
    summary="Show or set sticky _meta fields added to every request",
    usage="/meta                 show\n/meta key=val ...      set\n/meta clear            remove all",
    words=("clear",),
)
def cmd_meta(repl: Repl, rest: str) -> None:
    session = repl.session
    text = rest.strip()
    if text == "clear":
        session.meta_overrides.clear()
        print(dim("Sticky _meta cleared."))
    elif text:
        session.meta_overrides.update(parse_args(text))
    if session.meta_overrides:
        print(safe_json(session.meta_overrides))
    elif text != "clear":
        print(dim("No sticky _meta."))


@command(
    "/header",
    group="Protocol",
    summary="Show or set sticky HTTP headers",
    usage=(
        "/header                 show\n/header Name: value     set (empty value removes)\n"
        "/header clear           remove all"
    ),
    words=("clear",),
)
def cmd_header(repl: Repl, rest: str) -> None:
    wire = repl.session.transport.wire
    text = rest.strip()
    if repl.session.transport.kind == "stdio":
        print(dim("Note: stdio has no header layer; headers are only sent over HTTP."))
    if text == "clear":
        wire.extra_headers.clear()
    elif text:
        name, sep, value = text.partition(":")
        if not sep:
            print(red("Usage: /header Name: value"))
            return
        name, value = name.strip(), value.strip()
        if value:
            check_header(name, value)
            wire.extra_headers[name] = value
        else:
            wire.extra_headers.pop(name, None)
    if wire.extra_headers:
        for k, v in redact_headers(wire.extra_headers).items():
            print(f"  {sanitize(k)}: {sanitize(v)}")
    else:
        print(dim("No sticky headers."))
