# Copyright 2026 Phil Pennock — see LICENSE file.

"""Builtins for discovery and for listening to unsolicited server messages."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from ..color import bold, dim, red, yellow
from ..diagnostics import Level
from ..messages import Kind, as_obj, classify
from ..protocol import META_SUBSCRIPTION_ID
from ..sanitize import safe_json, sanitize
from ..transport import HttpStatusError, NoPendingMessages, ReceiveTimeout, TransportError
from . import command

if TYPE_CHECKING:
    from ..repl import Repl

_DEFAULT_LISTEN_SECS = 30.0
_FILTER_WORDS = {"tools": "toolsListChanged", "prompts": "promptsListChanged", "resources": "resourcesListChanged"}


@command("/discover", group="Server",
         summary="Send server/discover (2026-07-28) and show the result, in any era")
def cmd_discover(repl: Repl, rest: str) -> None:
    repl.show(repl.session.send_request("server/discover"))


def _parse_listen(rest: str) -> tuple[float, dict[str, Any]]:
    secs = _DEFAULT_LISTEN_SECS
    flt: dict[str, Any] = {}
    uris: list[str] = []
    for word in rest.split():
        if word.replace(".", "", 1).isdigit():
            secs = float(word)
        elif word in _FILTER_WORDS:
            flt[_FILTER_WORDS[word]] = True
        else:
            uris.append(word)
    if uris:
        flt["resourceSubscriptions"] = uris
    if not flt:
        flt = {v: True for v in _FILTER_WORDS.values()}
    return secs, flt


@command(
    "/listen",
    group="Server",
    summary="Listen for unsolicited server messages for a while (Ctrl-C stops)",
    usage=(
        "/listen [secs] [tools] [prompts] [resources] [uri ...]\n"
        "2026-07-28: opens subscriptions/listen with the given filter (default: all\n"
        "list changes; URIs subscribe to resource updates).\n"
        "Earlier revisions over HTTP: opens the GET listening stream.\n"
        "stdio, earlier revisions: just waits, showing whatever arrives."
    ),
    words=("tools", "prompts", "resources"),
)
def cmd_listen(repl: Repl, rest: str) -> None:
    secs, flt = _parse_listen(rest)
    session = repl.session
    if session.state.era == "modern":
        _listen_modern(repl, secs, flt)
        return
    transport = session.transport
    opened = False
    if transport.kind == "http":
        open_stream = getattr(transport, "open_listen_stream", None)
        if open_stream is not None:
            try:
                open_stream()
                opened = True
            except HttpStatusError as e:
                if e.status == 405:
                    print(dim("Server does not offer a GET listening stream (405); waiting on open streams only."))
                else:
                    raise
    print(dim(f"Listening for {secs:g}s (Ctrl-C to stop)..."))
    try:
        _drain_for(repl, secs)
    finally:
        if opened:
            getattr(transport, "close_listen_stream")()


def _drain_for(repl: Repl, secs: float, stop_id: Any = None) -> bool:
    """Show incoming messages until time runs out; True if stop_id's response came."""
    deadline = time.monotonic() + secs
    try:
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                msg = repl.session.transport.receive(timeout=min(remaining, 1.0))
            except ReceiveTimeout:
                continue
            except NoPendingMessages:
                time.sleep(min(remaining, 0.2))
                continue
            if stop_id is not None and classify(msg) == Kind.RESPONSE and as_obj(msg).get("id") == stop_id:
                return True
            repl.session.handle_incoming(msg)
    except KeyboardInterrupt:
        print()
    return False


def _listen_modern(repl: Repl, secs: float, flt: dict[str, Any]) -> None:
    session = repl.session
    req = session.build_request("subscriptions/listen", {"notifications": flt})
    sub_id = req["id"]
    acked: dict[str, Any] = {}

    def on_ack(msg: dict[str, Any]) -> None:
        if msg.get("method") != "notifications/subscriptions/acknowledged":
            return
        p = as_obj(msg.get("params"))
        if as_obj(p.get("_meta")).get(META_SUBSCRIPTION_ID) != sub_id:
            return
        acked.update(as_obj(p.get("notifications")))
        print(bold(f"Subscription {sub_id} acknowledged: ") + safe_json(acked, indent=None))
        declined = sorted(k for k in flt if k not in acked)
        if declined:
            print(dim(f"  server declined: {', '.join(declined)}"))
        extra = {k for k in acked if k not in flt}
        if extra:
            session.findings.violation(
                "subscription-extra-types",
                f"server acknowledged notification types we did not request: {', '.join(sorted(extra))}",
                "basic/patterns/subscriptions",
            )

    session.notification_listeners.append(on_ack)
    print(dim(f"subscriptions/listen {safe_json(flt, indent=None)} for {secs:g}s (Ctrl-C to stop)..."))
    try:
        session.transport.send(req)
        closed = _drain_for(repl, secs, stop_id=sub_id)
        if closed:
            print(yellow("The server closed the subscription."))
            return
        session.notify("notifications/cancelled", {"requestId": sub_id, "reason": "listen period over"})
        if session.transport.kind == "http":
            abandon = getattr(session.transport, "abandon_streams", None)
            if abandon is not None:
                abandon()
        elif not _drain_for(repl, 2.0, stop_id=sub_id):
            print(dim("(no closing response to the subscription)"))
        if not acked:
            session.findings.violation(
                "subscription-not-acknowledged",
                "server never sent notifications/subscriptions/acknowledged",
                "basic/patterns/subscriptions#acknowledgment",
                level=Level.WARNING,
            )
    except TransportError as e:
        print(red(f"Transport error: {sanitize(str(e))}"))
    finally:
        session.notification_listeners.remove(on_ack)
