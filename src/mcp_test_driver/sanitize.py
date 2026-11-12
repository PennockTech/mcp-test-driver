# Copyright 2026 Phil Pennock — see LICENSE file.

"""Neutralise server-supplied text before it reaches the terminal.

Every string an MCP server sends us (tool names, descriptions, results,
URLs, stderr) is untrusted.  See docs/security-model.md.
"""

from __future__ import annotations

import json
import re
from typing import Any

# C0 controls other than tab/newline/CR, DEL, and the 8-bit CSI (0x9B), which
# some terminals honour as an escape introducer.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x9b]")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b.")
# Bidirectional overrides and isolates can make displayed text read
# differently from what it is ("Trojan Source", CVE-2021-42574).
_BIDI_RE = re.compile("[‪-‮⁦-⁩]")


def sanitize(s: str) -> str:
    """Remove escape sequences, control characters and bidi overrides."""
    s = _ANSI_ESCAPE_RE.sub("", s)
    s = _CONTROL_RE.sub("", s)
    return _BIDI_RE.sub("", s)


def sanitize_any(value: Any) -> str:
    """Stringify any server-supplied value and sanitize it."""
    return sanitize(value if isinstance(value, str) else str(value))


def safe_json(obj: Any, *, indent: int | None = 2) -> str:
    """Pretty JSON for display.

    json.dumps escapes C0 controls inside strings, but with
    ensure_ascii=False it passes through 0x9B and bidi overrides, so the
    output is sanitized as well.
    """
    try:
        text = json.dumps(obj, indent=indent, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(obj)
    return sanitize(text)
