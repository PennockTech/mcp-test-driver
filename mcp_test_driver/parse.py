# Copyright 2026 Phil Pennock — see LICENSE file.

"""Argument parsing for MCP tool invocations."""

import json
import shlex


def parse_args(text: str) -> dict[str, object]:
    """Accept a JSON object literal or space-separated key=value pairs.

    Scalars are coerced: true/false → bool, numeric strings → int.
    """
    text = text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        return json.loads(text)  # type: ignore[no-any-return]

    result: dict[str, object] = {}
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
