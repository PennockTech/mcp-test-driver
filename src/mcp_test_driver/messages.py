# Copyright 2026 Phil Pennock — see LICENSE file.

"""JSON-RPC 2.0 message shapes and classification."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, cast

# Decoded JSON from a peer: genuinely dynamic, validated where used.
JsonValue = Any
JsonObject = dict[str, Any]
# A wire payload: one message, or (2025-03-26 only) a batch array.
Payload = JsonObject | list[JsonObject]


def as_obj(value: JsonValue) -> JsonObject:
    """The value if it is a JSON object, else an empty one."""
    return cast(JsonObject, value) if isinstance(value, dict) else {}


class Kind(StrEnum):
    RESPONSE = "response"
    NOTIFICATION = "notification"
    REQUEST = "request"
    INVALID = "invalid"


def classify(msg: JsonValue) -> Kind:
    """Sort an incoming message by JSON-RPC shape.

    A request has both method and id; a notification has a method and no id;
    a response has result or error and no method.  Anything else is invalid.
    """
    if not isinstance(msg, dict):
        return Kind.INVALID
    has_method = isinstance(msg.get("method"), str)
    if has_method and "id" in msg:
        return Kind.REQUEST
    if has_method:
        return Kind.NOTIFICATION
    if "result" in msg or "error" in msg:
        return Kind.RESPONSE
    return Kind.INVALID


def expand(payload: JsonValue) -> tuple[list[JsonValue], bool]:
    """Split a decoded wire payload into messages; report whether it was a batch."""
    if isinstance(payload, list):
        return list(payload), True
    return [payload], False


def request(req_id: int | str, method: str, params: JsonObject | None) -> JsonObject:
    msg: JsonObject = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def notification(method: str, params: JsonObject | None = None) -> JsonObject:
    msg: JsonObject = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def result(req_id: Any, value: JsonObject) -> JsonObject:
    return {"jsonrpc": "2.0", "id": req_id, "result": value}


def error(req_id: Any, code: int, message: str, data: Any = None) -> JsonObject:
    err: JsonObject = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}
