# Copyright 2026 Phil Pennock — see LICENSE file.

"""The connection report printed at start-up and by /info."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import versions
from .checks import check_session
from .color import bold, dim, green, yellow
from .sanitize import safe_json, sanitize, sanitize_any

if TYPE_CHECKING:
    from .protocol import McpSession
    from .repl import SessionCache

_INSTRUCTION_LINES = 8


def _all_server_capabilities() -> list[str]:
    seen: list[str] = []
    for spec in versions.SPECS.values():
        for c in sorted(spec.server_capabilities):
            if c not in seen:
                seen.append(c)
    return seen


def _subflags(value: Any) -> str:
    if isinstance(value, dict):
        flags = [f"{sanitize_any(k)}={sanitize_any(v) if not isinstance(v, dict) else '{…}'}" for k, v in value.items()]
        return ", ".join(flags)
    return safe_json(value, indent=None)


def capability_rows(caps: dict[str, Any], spec: versions.SpecVersion) -> list[tuple[str, str, str]]:
    """(capability, status, note) for every capability any revision defines, plus unknowns."""
    rows = []
    for key in _all_server_capabilities():
        intro = versions.introduced_capability("server", key) or ""
        in_spec = key in spec.server_capabilities
        note = "" if in_spec else (f"from {intro}" if intro > spec.name else f"not in {spec.name}")
        if key in caps:
            rows.append((key, "yes", (_subflags(caps[key]) + ("  " + note if note else "")).strip()))
        elif in_spec:
            rows.append((key, "-", ""))
    for key in caps:
        if versions.introduced_capability("server", key) is None:
            rows.append((sanitize_any(key), "yes", f"unknown  {_subflags(caps[key])}"))
    return rows


def print_report(
    session: McpSession, cache: SessionCache | None = None, *, all_findings: bool = False
) -> None:
    from .repl import print_findings

    state = session.state
    spec = session.spec
    if cache is not None:
        check_session(session, cache.lists)

    print(bold("Connection"))
    print(f"  transport  {sanitize(session.transport.describe())}")
    if session.transport.session_id:
        print(f"  session    Mcp-Session-Id {sanitize(session.transport.session_id)}")
    era = "stateless (modern)" if state.era == "modern" else "initialize handshake"
    version = state.version or "?"
    known = "" if versions.is_known(version) else yellow("  (unknown to this client)")
    print(f"  protocol   {bold(version)} via {era}{known}")
    if state.offered and state.offered != state.version:
        print(f"  offered    {state.offered}")
    if state.supported:
        print(f"  server supports  {', '.join(sanitize_any(v) for v in state.supported)}")
    for line in state.how_chosen:
        print(dim(f"    {sanitize(line)}"))

    info = state.server_info
    print(bold("Server"))
    name = sanitize_any(info.get("name", "?"))
    ver = sanitize_any(info.get("version", "?"))
    title = info.get("title")
    print(f"  {name} {ver}" + (f"  ({sanitize_any(title)})" if title else ""))
    if info.get("description"):
        print(f"  {dim(sanitize_any(info['description']))}")
    if info.get("websiteUrl"):
        print(f"  {dim(sanitize_any(info['websiteUrl']))}")
    if isinstance(info.get("icons"), list) and info["icons"]:
        print(dim(f"  {len(info['icons'])} icon(s)"))
    if state.instructions:
        lines = sanitize(state.instructions).splitlines()
        print(bold("Instructions"))
        for line in lines[:_INSTRUCTION_LINES]:
            print(f"  {dim(line)}")
        if len(lines) > _INSTRUCTION_LINES:
            print(dim(f"  … {len(lines) - _INSTRUCTION_LINES} more line(s)"))

    print(bold("Server capabilities"))
    for key, status, note in capability_rows(state.capabilities, spec):
        mark = green(status) if status == "yes" else dim(status)
        print(f"  {key:<13} {mark:<4} {dim(note)}")
    client_caps = state.client_capabilities or session.client_capabilities()
    print(bold("Client capabilities sent"))
    print(f"  {dim(safe_json(client_caps, indent=None)) if client_caps else dim('(none)')}")

    if cache is not None:
        counts = [
            f"{len(cache.lists[k])} {label}"
            for k, label in (("tools", "tools"), ("resources", "resources"),
                             ("templates", "templates"), ("prompts", "prompts"))
            if k in cache.lists
        ]
        if counts:
            print(bold("Lists"))
            print(f"  {', '.join(counts)}")

    findings = session.findings.take_unshown()
    if all_findings:
        findings = list(session.findings)
    print(bold("Findings"))
    if findings:
        print_findings(findings)
    else:
        print(dim("  none"))
