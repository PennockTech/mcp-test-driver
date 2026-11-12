# Copyright 2026 Phil Pennock — see LICENSE file.

"""Passive conformance checks over what a server has told us.

Each check compares an observation with the revision the server claims.
Features from later revisions are recorded as "beyond-claimed" (useful to
know, not wrong); things the claimed revision forbids are violations.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

from . import versions
from .diagnostics import DiagnosticLog, Level
from .sanitize import sanitize_any
from .transport.params import analyse as analyse_header_params
from .versions import Feature, SpecVersion

if TYPE_CHECKING:
    from .protocol import McpSession

# SEP-986 (2025-11-25): 1-128 chars of [A-Za-z0-9_.-], case-sensitive, unique.
_TOOL_NAME_RE = re.compile(r"[A-Za-z0-9_.\-]{1,128}")


def _beyond(log: DiagnosticLog, spec: SpecVersion, feature: Feature, what: str) -> None:
    intro = versions.introduced_feature(feature)
    if intro and intro > spec.name:
        log.beyond(f"beyond-{feature.name.lower()}", f"{what} ({feature}, from {intro}) while claiming {spec.name}")


def check_capabilities(
    log: DiagnosticLog, spec: SpecVersion, caps: dict[str, Any], side: Literal["client", "server"] = "server"
) -> None:
    allowed = spec.server_capabilities if side == "server" else spec.client_capabilities
    for key in caps:
        if key in allowed:
            continue
        intro = versions.introduced_capability(side, key)
        if intro is None:
            log.note(
                f"unknown-{side}-capability",
                f"{side} capability {sanitize_any(key)!r} is not defined by any known revision",
            )
        elif intro > spec.name:
            log.beyond(
                f"{side}-capability-beyond-version",
                f"{side} capability {key!r} is from {intro}; server claims {spec.name}",
            )
        else:
            log.violation(
                f"{side}-capability-removed",
                f"{side} capability {key!r} is not part of {spec.name}",
                "basic/lifecycle#capability-negotiation",
                level=Level.WARNING,
            )
    for key in ("logging", "sampling", "roots"):
        if key in caps and key in spec.deprecated:
            log.deprecated(f"deprecated-{key}", f"{side} uses {key}, deprecated in {spec.name}")
    for key, value in caps.items():
        if key not in ("experimental", "extensions") and value is not None and not isinstance(value, dict):
            log.violation(
                "capability-not-object",
                f"{side} capability {sanitize_any(key)!r} should be an object, got {type(value).__name__}",
                "schema",
                level=Level.WARNING,
            )


def check_implementation(log: DiagnosticLog, spec: SpecVersion, info: dict[str, Any]) -> None:
    if "name" not in info:
        log.violation("server-info-no-name", "serverInfo lacks the required name", "schema#implementation")
    if "version" not in info:
        log.violation("server-info-no-version", "serverInfo lacks the required version", "schema#implementation")
    if "title" in info:
        _beyond(log, spec, Feature.TITLES, "serverInfo.title")
    if "icons" in info:
        _beyond(log, spec, Feature.ICONS, "serverInfo.icons")
    if "description" in info or "websiteUrl" in info:
        _beyond(log, spec, Feature.IMPLEMENTATION_DETAILS, "serverInfo description/websiteUrl")


def check_tools(log: DiagnosticLog, spec: SpecVersion, tools: list[dict[str, Any]]) -> None:
    names: dict[str, int] = {}
    rules_apply = spec.has(Feature.TOOL_NAME_RULES)
    for t in tools:
        name = t.get("name")
        if not isinstance(name, str) or not name:
            log.violation("tool-no-name", "a tool has no name", "server/tools")
            continue
        shown = sanitize_any(name)
        names[name] = names.get(name, 0) + 1
        if not _TOOL_NAME_RE.fullmatch(name):
            msg = f"tool name {shown!r} breaks the SEP-986 format (1-128 of A-Z a-z 0-9 _ . -)"
            if rules_apply:
                log.violation("tool-name-format", msg, "server/tools#tool-names", level=Level.WARNING)
            else:
                log.note("tool-name-format", msg + "; the rule dates from 2025-11-25")
        schema = t.get("inputSchema")
        if not isinstance(schema, dict):
            log.violation("tool-input-schema", f"tool {shown!r} has no inputSchema object", "server/tools")
        elif schema.get("type") != "object":
            log.violation(
                "tool-input-schema-type",
                f"tool {shown!r} inputSchema type is {sanitize_any(schema.get('type'))!r}, not 'object'",
                "server/tools",
                level=Level.WARNING,
            )
        if isinstance(schema, dict):
            hp = analyse_header_params(schema)
            if hp.paths or hp.problems:
                _beyond(log, spec, Feature.METHOD_HEADERS, f"tool {shown!r} x-mcp-header")
            for problem in hp.problems:
                log.violation(
                    "tool-x-mcp-header",
                    f"tool {shown!r}: {sanitize_any(problem)}; a conforming HTTP client must drop this tool",
                    "basic/transports/streamable-http#custom-headers-from-tool-parameters",
                )
        if "annotations" in t:
            _beyond(log, spec, Feature.TOOL_ANNOTATIONS, f"tool {shown!r} annotations")
        if "outputSchema" in t:
            _beyond(log, spec, Feature.STRUCTURED_OUTPUT, f"tool {shown!r} outputSchema")
            out = t["outputSchema"]
            if spec.name < "2026-07-28" and isinstance(out, dict) and out.get("type") != "object":
                log.violation(
                    "tool-output-schema-type",
                    f"tool {shown!r} outputSchema type must be 'object' before 2026-07-28",
                    "server/tools#output-schema",
                    level=Level.WARNING,
                )
        if "title" in t:
            _beyond(log, spec, Feature.TITLES, f"tool {shown!r} title")
        if "icons" in t:
            _beyond(log, spec, Feature.ICONS, f"tool {shown!r} icons")
        if "_meta" in t:
            _beyond(log, spec, Feature.META_FIELDS, f"tool {shown!r} _meta")
        if "execution" in t and not spec.has(Feature.TASKS_CORE):
            if spec.era == "modern":
                log.note(
                    "tool-execution-extension",
                    f"tool {shown!r} has execution; core tasks moved to the tasks extension in 2026-07-28",
                )
            else:
                _beyond(log, spec, Feature.TASKS_CORE, f"tool {shown!r} execution.taskSupport")
    dupes = [n for n, c in names.items() if c > 1]
    for n in dupes:
        log.violation("tool-name-duplicate", f"tool name {sanitize_any(n)!r} appears {names[n]} times", "server/tools")


def check_named_items(log: DiagnosticLog, spec: SpecVersion, kind: str, items: list[dict[str, Any]]) -> None:
    key = "uriTemplate" if kind == "resource template" else "uri" if kind == "resource" else "name"
    for item in items:
        ident = item.get(key)
        if not isinstance(ident, str) or not ident:
            log.violation(f"{kind.replace(' ', '-')}-no-{key.lower()}", f"a {kind} has no {key}", "schema")
            continue
        shown = sanitize_any(ident)
        if kind != "prompt" and not isinstance(item.get("name"), str):
            log.violation(f"{kind.replace(' ', '-')}-no-name", f"{kind} {shown!r} has no name", "schema")
        if "title" in item:
            _beyond(log, spec, Feature.TITLES, f"{kind} {shown!r} title")
        if "icons" in item:
            _beyond(log, spec, Feature.ICONS, f"{kind} {shown!r} icons")


def check_session(session: McpSession, lists: dict[str, list[dict[str, Any]]]) -> None:
    """Everything passive we can say after connecting and listing."""
    log = session.findings
    spec = session.spec
    state = session.state
    check_capabilities(log, spec, state.capabilities)
    check_implementation(log, spec, state.server_info)
    check_tools(log, spec, lists.get("tools", []))
    check_named_items(log, spec, "resource", lists.get("resources", []))
    check_named_items(log, spec, "resource template", lists.get("templates", []))
    check_named_items(log, spec, "prompt", lists.get("prompts", []))
    sid = session.transport.session_id
    if sid and not spec.has(Feature.SESSIONS):
        log.violation(
            "session-id-without-sessions",
            f"server issued Mcp-Session-Id but {spec.name} has no sessions",
            "basic/transports/streamable-http",
        )
    batches = getattr(session.transport, "batches_received", 0)
    if batches and not spec.has(Feature.BATCHING):
        log.violation(
            "batch-not-allowed",
            f"server sent {batches} JSON-RPC batch(es); only 2025-03-26 allows batching",
            "basic",
            level=Level.WARNING,
        )


def check_tool_result(log: DiagnosticLog, spec: SpecVersion, tool: dict[str, Any], result: Any) -> None:
    """Structured output (2025-06-18+): a tool with an outputSchema MUST return
    structuredContent conforming to it.  Validation uses the optional
    `jsonschema` package when it is installed."""
    if not isinstance(result, dict) or result.get("isError"):
        return
    schema = tool.get("outputSchema")
    name = sanitize_any(tool.get("name", "?"))
    if not isinstance(schema, dict):
        return
    if "structuredContent" not in result:
        log.violation("missing-structured-content",
                      f"tool {name!r} has an outputSchema but returned no structuredContent",
                      "server/tools#output-schema")
        return
    try:
        import jsonschema  # ty: ignore[unresolved-import]
    except ImportError:
        log.note("structured-content-unvalidated",
                 "install the jsonschema package to validate structuredContent against outputSchema")
        return
    try:
        jsonschema.validate(result["structuredContent"], schema)
    except jsonschema.ValidationError as e:
        log.violation("structured-content-invalid",
                      f"tool {name!r} structuredContent does not match its outputSchema: {sanitize_any(e.message)}",
                      "server/tools#output-schema")
    except jsonschema.SchemaError as e:
        log.violation("output-schema-invalid", f"tool {name!r} outputSchema is not valid: {sanitize_any(e.message)}",
                      "server/tools#output-schema")
