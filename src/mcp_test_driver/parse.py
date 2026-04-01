# Copyright 2026 Phil Pennock — see LICENSE file.

"""Argument parsing for MCP tool invocations."""

import json
import shlex
from typing import Any

# Guard against pathologically nested input exhausting the call stack.
_MAX_DEPTH = 20


def parse_args(text: str) -> dict[str, Any]:
    """Accept a JSON object literal or space-separated key=value pairs.

    Value types accepted in key=value form:
      key=scalar        bool (true/false), int, or string
      key=[a b c]       list; items separated by whitespace and/or commas
      key={k: v ...}    dict; keys and simple values need not be quoted
      key="a b"         quoted string (shell quoting rules)

    Lists and dicts may be nested.

    A bare top-level {…} is tried as strict JSON first so that existing
    callers using {"key": val} continue to work; if JSON parsing fails it
    falls back to the relaxed dict syntax.
    """
    text = text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            result = json.loads(text)
            if not isinstance(result, dict):
                raise ValueError(f"Expected a JSON object, got {type(result).__name__}")
            return result
        except json.JSONDecodeError:
            pass
        # Relaxed dict syntax: {key: val, ...}
        if not text.endswith("}"):
            raise ValueError("Unmatched '{' in argument")
        return _parse_dict_content(text[1:-1], depth=0)

    result: dict[str, Any] = {}
    for tok in _split_at_depth0(text, " \t\n,"):
        tok = tok.strip()
        if not tok or "=" not in tok:
            continue
        key, _, val_str = tok.partition("=")
        if not key:
            continue
        result[key] = _parse_value(val_str, depth=0)
    return result


def _parse_value(s: str, depth: int) -> Any:
    """Parse a single value token into its Python equivalent."""
    if depth > _MAX_DEPTH:
        return s
    s = s.strip()
    if s.startswith("["):
        if not s.endswith("]"):
            raise ValueError(f"Unmatched '[' in value: {s!r}")
        return _parse_list_content(s[1:-1], depth + 1)
    if s.startswith("{"):
        if not s.endswith("}"):
            raise ValueError(f"Unmatched '{{' in value: {s!r}")
        return _parse_dict_content(s[1:-1], depth + 1)
    if len(s) >= 2 and s[0] in ('"', "'") and s[-1] == s[0]:
        try:
            return shlex.split(s)[0]
        except ValueError:
            return s
    return _coerce_scalar(s)


def _parse_list_content(content: str, depth: int) -> list[Any]:
    """Parse the body of a [...] literal into a Python list."""
    if depth > _MAX_DEPTH:
        return []
    return [
        _parse_value(item, depth)
        for item in _split_at_depth0(content, " \t\n,")
        if item.strip()
    ]


def _parse_dict_content(content: str, depth: int) -> dict[str, Any]:
    """Parse the body of a {…} literal into a Python dict.

    Accepts both 'key: value' (space after colon) and 'key:value' (no space).
    Keys and unquoted values follow the same coercion rules as scalars.
    """
    if depth > _MAX_DEPTH:
        return {}
    result: dict[str, Any] = {}
    tokens = _split_at_depth0(content, " \t\n,")
    i = 0
    while i < len(tokens):
        tok = tokens[i].strip()
        if not tok:
            i += 1
            continue
        if ":" not in tok:
            i += 1
            continue
        colon = tok.index(":")
        key = tok[:colon].strip()
        remainder = tok[colon + 1 :].strip()
        if not key:
            i += 1
            continue
        if remainder:
            # key:value — value is in the same token
            result[key] = _parse_value(remainder, depth + 1)
            i += 1
        else:
            # key: value — value is the next token
            i += 1
            if i < len(tokens):
                result[key] = _parse_value(tokens[i].strip(), depth + 1)
                i += 1
            else:
                result[key] = ""
    return result


def _coerce_scalar(s: str) -> bool | int | str:
    """Coerce a plain string to bool, int, or str."""
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    try:
        return int(s)
    except ValueError:
        return s


def _split_at_depth0(text: str, stop_chars: str) -> list[str]:
    """Tokenise text, splitting on stop_chars only at bracket/brace depth 0.

    Quoted strings (single or double, shell rules) are treated as atomic
    tokens regardless of their content.
    """
    tokens: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in ('"', "'"):
            q = c
            buf.append(c)
            i += 1
            while i < n:
                ch = text[i]
                if ch == "\\" and q == '"':
                    buf.append(ch)
                    i += 1
                    if i < n:
                        buf.append(text[i])
                        i += 1
                elif ch == q:
                    buf.append(ch)
                    i += 1
                    break
                else:
                    buf.append(ch)
                    i += 1
        elif c in ("[", "{"):
            depth += 1
            buf.append(c)
            i += 1
        elif c in ("]", "}"):
            depth -= 1
            buf.append(c)
            i += 1
        elif depth == 0 and c in stop_chars:
            if buf:
                tokens.append("".join(buf))
                buf = []
            i += 1
        else:
            buf.append(c)
            i += 1
    if buf:
        tokens.append("".join(buf))
    return tokens
