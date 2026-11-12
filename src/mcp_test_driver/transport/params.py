# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tool parameters mirrored into HTTP headers (`x-mcp-header`, SEP-2243, 2026-07-28)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ..messages import as_obj

# RFC 9110 field-name token.
_TOKEN_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_SAFE_INT = 2**53 - 1
_ALLOWED_TYPES = {"string", "integer", "boolean"}


@dataclass
class HeaderParams:
    """Annotated parameters of one tool: property path → header name."""

    paths: dict[tuple[str, ...], str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.problems


def _all_annotations(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Every x-mcp-header anywhere in a schema, with the JSON path to it."""
    if isinstance(node, dict):
        if "x-mcp-header" in node:
            yield path, node["x-mcp-header"]
        for k, v in node.items():
            yield from _all_annotations(v, (*path, str(k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _all_annotations(v, (*path, str(i)))


def analyse(schema: Any) -> HeaderParams:
    """Find and validate x-mcp-header annotations in a tool's inputSchema."""
    out = HeaderParams()
    seen: dict[str, tuple[str, ...]] = {}
    for json_path, name in _all_annotations(schema):
        # Statically reachable means: properties/<k>/properties/<k>/... only.
        keys = json_path[1::2]
        if len(json_path) % 2 or any(step != "properties" for step in json_path[0::2]) or not json_path:
            out.problems.append(f"x-mcp-header at {'/'.join(json_path) or '(root)'} is not reachable via properties alone")
            continue
        prop: dict[str, Any] = as_obj(schema)
        for k in keys:
            prop = as_obj(as_obj(prop.get("properties")).get(k))
        if not isinstance(name, str) or not _TOKEN_RE.fullmatch(name):
            out.problems.append(f"x-mcp-header {name!r} on {'.'.join(keys)} is not an HTTP token")
            continue
        if prop.get("type") not in _ALLOWED_TYPES:
            out.problems.append(f"x-mcp-header {name!r} is on a {prop.get('type')!r} property; only string, integer, boolean")
            continue
        lower = name.lower()
        if lower in seen:
            out.problems.append(f"x-mcp-header {name!r} is not unique (case-insensitively)")
            continue
        seen[lower] = keys
        out.paths[keys] = name
    return out


def encode_value(value: Any) -> str | None:
    """The header text for a primitive parameter value, or None if not encodable."""
    from .http import encode_header_value

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value) if abs(value) <= _SAFE_INT else None
    if isinstance(value, str):
        return encode_header_value(value)
    return None


def param_headers(schema: Any, arguments: dict[str, Any]) -> dict[str, str]:
    """Mcp-Param-* headers for a call; omitted where the argument is absent."""
    headers: dict[str, str] = {}
    for keys, name in analyse(schema).paths.items():
        value: Any = arguments
        for k in keys:
            value = value.get(k) if isinstance(value, dict) else None
        if value is None:
            continue
        text = encode_value(value)
        if text is not None:
            headers[f"Mcp-Param-{name}"] = text
    return headers
