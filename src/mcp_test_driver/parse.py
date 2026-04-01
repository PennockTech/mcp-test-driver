# Copyright 2026 Phil Pennock — see LICENSE file.

"""Argument parsing for MCP tool invocations."""

import json
import shlex
from typing import Any


def parse_args(text: str) -> dict[str, Any]:
    """Accept a JSON object literal or space-separated key=value pairs.

    Scalars are coerced: true/false → bool, numeric strings → int.
    """
    text = text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError(f"Expected a JSON object, got {type(result).__name__}")
        return result

    result: dict[str, Any] = {}
    for token in shlex.split(text):
        if "=" not in token:
            continue
        key, _, val = token.partition("=")
        if val.lower() == "true":
            result[key] = True
        elif val.lower() == "false":
            result[key] = False
        else:
            try:
                result[key] = int(val)
            except ValueError:
                result[key] = val
    return result
