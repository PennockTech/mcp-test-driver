# Copyright 2026 Phil Pennock — see LICENSE file.

"""Human-readable rendering of MCP results.

Everything printed here comes from the server, so every string goes through
sanitize() / safe_json().  Rendering is generous: fields we don't recognise
(newer revisions, extensions, plain mistakes) are shown, not dropped.
"""

from __future__ import annotations

import json
from typing import Any

from . import versions
from .color import bold, dim, green, red
from .messages import JsonValue
from .sanitize import safe_json, sanitize, sanitize_any

_ERROR_NAMES = {
    **versions.STANDARD_ERRORS,
    versions.RESOURCE_NOT_FOUND_LEGACY: "Resource not found (pre-2026)",
    versions.URL_ELICITATION_REQUIRED: "URL elicitation required",
    versions.HEADER_MISMATCH: "Header mismatch",
    versions.MISSING_REQUIRED_CLIENT_CAPABILITY: "Missing required client capability",
    versions.UNSUPPORTED_PROTOCOL_VERSION: "Unsupported protocol version",
}

# Top-level keys each result shape is expected to carry; anything else is
# shown under "other fields".
_TOOL_RESULT_KEYS = {"content", "structuredContent", "isError", "_meta", "resultType"}
_PROMPT_RESULT_KEYS = {"description", "messages", "_meta", "resultType", "ttlMs", "cacheScope"}
_READ_RESULT_KEYS = {"contents", "_meta", "resultType", "ttlMs", "cacheScope"}


def error_name(code: JsonValue) -> str:
    if isinstance(code, int):
        name = _ERROR_NAMES.get(code)
        if name:
            return name
        if -32099 <= code <= -32000:
            return "server error"
    return ""


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB"):
        if size < 1024 or unit == "MiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def _b64_size(data: JsonValue) -> str:
    if not isinstance(data, str):
        return "no data"
    stripped = data.rstrip("=")
    return human_size(len(stripped) * 3 // 4)


def _out(text: str, indent: int) -> None:
    pad = " " * indent
    for line in text.splitlines() or [""]:
        print(pad + line)


def _text(text: str, indent: int, *, colour: Any = None) -> None:
    """Text content: pretty-print if it is JSON, else show as-is."""
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            _out(safe_json(json.loads(stripped)), indent)
            return
        except json.JSONDecodeError:
            pass
    clean = sanitize(text)
    _out(colour(clean) if colour else clean, indent)


def _annotations(block: dict[str, Any]) -> str:
    ann = block.get("annotations")
    if not isinstance(ann, dict) or not ann:
        return ""
    parts = []
    for k, v in ann.items():
        parts.append(f"{sanitize_any(k)}={sanitize_any(v if not isinstance(v, list) else ','.join(map(str, v)))}")
    return dim(f"  ({'; '.join(parts)})")


def render_block(block: JsonValue, indent: int = 0, *, is_error: bool = False) -> None:
    if not isinstance(block, dict):
        _out(safe_json(block), indent)
        return
    btype = block.get("type")
    pad = " " * indent
    if btype == "text":
        text = block.get("text", "")
        if is_error:
            _text(f"Error: {text}" if isinstance(text, str) else safe_json(text), indent, colour=red)
        else:
            _text(text if isinstance(text, str) else safe_json(text), indent)
        extra = _annotations(block)
        if extra:
            print(pad + extra.strip())
        return
    if btype in ("image", "audio"):
        mime = sanitize_any(block.get("mimeType", "?"))
        print(f"{pad}[{btype} {mime}, {_b64_size(block.get('data'))}]{_annotations(block)}")
        return
    if btype == "resource":
        res = block.get("resource")
        print(f"{pad}[embedded resource]{_annotations(block)}")
        render_resource_contents(res, indent + 2)
        return
    if btype == "resource_link":
        uri = sanitize_any(block.get("uri", "?"))
        name = block.get("title") or block.get("name")
        line = f"{pad}[link] {bold(uri)}"
        if name:
            line += f"  {sanitize_any(name)}"
        if block.get("mimeType"):
            line += dim(f"  [{sanitize_any(block['mimeType'])}]")
        print(line + _annotations(block))
        if block.get("description"):
            print(f"{pad}  {dim(sanitize_any(block['description']))}")
        return
    if btype == "tool_use":
        name = sanitize_any(block.get("name", "?"))
        print(f"{pad}[tool_use {sanitize_any(block.get('id', '?'))}] {bold(name)}")
        _out(safe_json(block.get("input", {})), indent + 2)
        return
    if btype == "tool_result":
        print(f"{pad}[tool_result for {sanitize_any(block.get('toolUseId', '?'))}]")
        for inner in block.get("content") or []:
            render_block(inner, indent + 2, is_error=bool(block.get("isError")))
        return
    print(f"{pad}[{sanitize_any(btype)} content]")
    _out(safe_json(block), indent + 2)


def render_resource_contents(res: JsonValue, indent: int = 0) -> None:
    pad = " " * indent
    if not isinstance(res, dict):
        _out(safe_json(res), indent)
        return
    uri = sanitize_any(res.get("uri", "?"))
    mime = res.get("mimeType")
    head = f"{pad}{bold(uri)}"
    if mime:
        head += dim(f"  [{sanitize_any(mime)}]")
    print(head)
    if isinstance(res.get("text"), str):
        _text(res["text"], indent + 2)
    elif "blob" in res:
        print(f"{pad}  [binary, {_b64_size(res.get('blob'))}]")
    else:
        _out(safe_json(res), indent + 2)


def _other_fields(result: dict[str, Any], known: set[str]) -> None:
    extra = {k: v for k, v in result.items() if k not in known}
    if extra:
        print(dim("other fields:"))
        _out(dim(safe_json(extra)), 2)


def _meta_and_cache(result: dict[str, Any]) -> None:
    meta = result.get("_meta")
    if isinstance(meta, dict) and meta:
        print(dim(f"_meta: {safe_json(meta, indent=None)}"))
    if "ttlMs" in result or "cacheScope" in result:
        print(dim(f"cache: ttlMs={sanitize_any(result.get('ttlMs'))} scope={sanitize_any(result.get('cacheScope'))}"))


def render_error(err: JsonValue) -> None:
    if not isinstance(err, dict):
        print(red(f"Server error: {safe_json(err, indent=None)}"))
        return
    code = err.get("code", "?")
    name = error_name(code)
    label = f"{sanitize_any(code)}{' ' + name if name else ''}"
    print(red(f"Server error [{label}]: {sanitize_any(err.get('message', 'Unknown error'))}"))
    data = err.get("data")
    if code == versions.URL_ELICITATION_REQUIRED and isinstance(data, dict):
        render_url_elicitations(data.get("elicitations"))
    elif data is not None:
        _out(red(safe_json(data)), 2)


def render_url_elicitations(elicitations: JsonValue) -> None:
    """-32042: the server needs the user to visit URLs before retrying."""
    print(dim("  The server needs you to complete these first, then retry:"))
    for e in elicitations if isinstance(elicitations, list) else []:
        if not isinstance(e, dict):
            continue
        print(f"    {bold(sanitize_any(e.get('url', '?')))}")
        if e.get("message"):
            print(f"      {sanitize_any(e['message'])}")


def render_result(result: JsonValue) -> None:
    """Render the `result` member of a response, whatever its shape."""
    if not isinstance(result, dict):
        if result is not None:
            _out(safe_json(result), 0)
        return
    if result.get("resultType") == "input_required":
        print(dim("[input required]"))
        _out(safe_json(result), 2)
        return
    if isinstance(result.get("content"), list):
        is_error = bool(result.get("isError"))
        for block in result["content"]:
            render_block(block, is_error=is_error)
        if "structuredContent" in result:
            print(green("structuredContent:"))
            _out(safe_json(result["structuredContent"]), 2)
        _meta_and_cache(result)
        _other_fields(result, _TOOL_RESULT_KEYS)
        return
    if isinstance(result.get("messages"), list):
        if result.get("description"):
            print(dim(sanitize_any(result["description"])))
        for msg in result["messages"]:
            if not isinstance(msg, dict):
                _out(safe_json(msg), 0)
                continue
            print(bold(f"[{sanitize_any(msg.get('role', '?'))}]"))
            content = msg.get("content")
            for block in content if isinstance(content, list) else [content]:
                render_block(block, 2)
        _meta_and_cache(result)
        _other_fields(result, _PROMPT_RESULT_KEYS)
        return
    if isinstance(result.get("contents"), list):
        for res in result["contents"]:
            render_resource_contents(res)
        _meta_and_cache(result)
        _other_fields(result, _READ_RESULT_KEYS)
        return
    if result:
        _out(safe_json(result), 0)


def render_response(resp: JsonValue) -> None:
    """Render a whole JSON-RPC response message."""
    if not isinstance(resp, dict):
        _out(safe_json(resp), 0)
        return
    if "error" in resp:
        render_error(resp["error"])
        return
    render_result(resp.get("result"))
