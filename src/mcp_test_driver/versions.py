# Copyright 2026 Phil Pennock — see LICENSE file.

"""Registry of MCP specification revisions and what each one defines.

This is docs/mcp-feature-matrix.md in executable form.  It decides *defaults*
(which wire format to speak) and *annotations* (which revision introduced a
feature we observed); it never gates what a user may send.

tests/test_versions.py cross-checks the method and capability sets here
against the vendored schemas in docs/specs/, so a spec refresh that changes
them fails until this file is updated.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from collections.abc import Mapping, Set as AbstractSet
from typing import Any, Literal

Era = Literal["handshake", "modern"]


class Feature(StrEnum):
    """Behaviours that are not visible as a method or capability name."""

    HANDSHAKE = "initialize handshake"
    LEGACY_SSE = "HTTP+SSE transport"
    STREAMABLE_HTTP = "Streamable HTTP transport"
    BATCHING = "JSON-RPC batching"
    SESSIONS = "Mcp-Session-Id sessions"
    GET_STREAM = "HTTP GET listening stream"
    SSE_RESUME = "SSE resumption (Last-Event-ID)"
    SSE_POLLING = "SSE polling (priming event, retry)"
    PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version header"
    METHOD_HEADERS = "Mcp-Method / Mcp-Name headers"
    PER_REQUEST_META = "per-request protocol _meta"
    SERVER_DISCOVER = "server/discover"
    MRTR = "multi round-trip requests"
    RESULT_TYPE = "resultType on results"
    CACHEABLE_RESULTS = "ttlMs / cacheScope on results"
    SUBSCRIPTIONS_LISTEN = "subscriptions/listen"
    OAUTH = "OAuth 2.1 authorization"
    PROTECTED_RESOURCE_METADATA = "RFC 9728 protected resource metadata"
    TOOL_ANNOTATIONS = "tool annotations"
    AUDIO = "audio content"
    PROGRESS_MESSAGE = "progress message"
    COMPLETIONS_CAPABILITY = "completions capability"
    STRUCTURED_OUTPUT = "outputSchema / structuredContent"
    TITLES = "title fields"
    RESOURCE_LINKS = "resource_link content"
    META_FIELDS = "_meta on tools, resources, prompts and content"
    ELICITATION = "elicitation (form mode)"
    COMPLETION_CONTEXT = "completion context"
    ICONS = "icons"
    IMPLEMENTATION_DETAILS = "Implementation description / websiteUrl"
    TOOL_NAME_RULES = "tool name format rules"
    ELICITATION_URL = "elicitation (URL mode)"
    ELICITATION_COMPLETE = "notifications/elicitation/complete"
    TASKS_CORE = "tasks (core, experimental)"
    SAMPLING_TOOLS = "sampling with tools"
    JSON_SCHEMA_2020_12 = "JSON Schema 2020-12 default dialect"
    EXTENSIONS = "extensions capability"
    OTEL_META = "OpenTelemetry trace context in _meta"


# Error codes defined by the spec beyond the JSON-RPC standard ones.
RESOURCE_NOT_FOUND_LEGACY = -32002
URL_ELICITATION_REQUIRED = -32042
HEADER_MISMATCH = -32020
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022

STANDARD_ERRORS = {
    -32700: "Parse error",
    -32600: "Invalid request",
    -32601: "Method not found",
    -32602: "Invalid params",
    -32603: "Internal error",
}


@dataclass(frozen=True)
class SpecVersion:
    name: str
    era: Era
    # C→S requests.
    client_requests: frozenset[str]
    # S→C request shapes.  From 2026-07-28 these are embedded in
    # InputRequiredResult rather than sent as JSON-RPC requests.
    server_requests: frozenset[str]
    client_notifications: frozenset[str]
    server_notifications: frozenset[str]
    client_capabilities: frozenset[str]
    server_capabilities: frozenset[str]
    content_types: frozenset[str]
    features: frozenset[Feature]
    deprecated: frozenset[str] = field(default_factory=frozenset)
    error_codes: frozenset[int] = field(default_factory=frozenset)

    @property
    def methods(self) -> frozenset[str]:
        return (
            self.client_requests
            | self.server_requests
            | self.client_notifications
            | self.server_notifications
        )

    def has(self, feature: Feature) -> bool:
        return feature in self.features

    def defines_method(self, method: str) -> bool:
        return method in self.methods


def _evolve(
    prev: SpecVersion,
    name: str,
    *,
    era: Era | None = None,
    add: Mapping[str, AbstractSet[Any]] | None = None,
    remove: Mapping[str, AbstractSet[Any]] | None = None,
) -> SpecVersion:
    """Derive a revision from its predecessor, mirroring its changelog."""
    changes: dict[str, frozenset] = {}
    for attr in (
        "client_requests",
        "server_requests",
        "client_notifications",
        "server_notifications",
        "client_capabilities",
        "server_capabilities",
        "content_types",
        "features",
        "deprecated",
        "error_codes",
    ):
        cur = set(getattr(prev, attr))
        cur |= (add or {}).get(attr, set())
        cur -= (remove or {}).get(attr, set())
        changes[attr] = frozenset(cur)
    return replace(prev, name=name, era=era or prev.era, **changes)


_TASKS = {"tasks/get", "tasks/result", "tasks/list", "tasks/cancel"}

V2024_11_05 = SpecVersion(
    name="2024-11-05",
    era="handshake",
    client_requests=frozenset(
        {
            "initialize",
            "ping",
            "resources/list",
            "resources/templates/list",
            "resources/read",
            "resources/subscribe",
            "resources/unsubscribe",
            "prompts/list",
            "prompts/get",
            "tools/list",
            "tools/call",
            "logging/setLevel",
            "completion/complete",
        }
    ),
    server_requests=frozenset({"ping", "sampling/createMessage", "roots/list"}),
    client_notifications=frozenset(
        {
            "notifications/initialized",
            "notifications/cancelled",
            "notifications/progress",
            "notifications/roots/list_changed",
        }
    ),
    server_notifications=frozenset(
        {
            "notifications/cancelled",
            "notifications/progress",
            "notifications/message",
            "notifications/resources/updated",
            "notifications/resources/list_changed",
            "notifications/tools/list_changed",
            "notifications/prompts/list_changed",
        }
    ),
    client_capabilities=frozenset({"experimental", "roots", "sampling"}),
    server_capabilities=frozenset(
        {"experimental", "logging", "prompts", "resources", "tools"}
    ),
    content_types=frozenset({"text", "image", "resource"}),
    features=frozenset({Feature.HANDSHAKE, Feature.LEGACY_SSE}),
    error_codes=frozenset({RESOURCE_NOT_FOUND_LEGACY}),
)

V2025_03_26 = _evolve(
    V2024_11_05,
    "2025-03-26",
    add={
        "server_capabilities": {"completions"},
        "content_types": {"audio"},
        "features": {
            Feature.STREAMABLE_HTTP,
            Feature.BATCHING,
            Feature.SESSIONS,
            Feature.GET_STREAM,
            Feature.SSE_RESUME,
            Feature.OAUTH,
            Feature.TOOL_ANNOTATIONS,
            Feature.AUDIO,
            Feature.PROGRESS_MESSAGE,
            Feature.COMPLETIONS_CAPABILITY,
        },
        "deprecated": {"HTTP+SSE transport"},
    },
    remove={"features": {Feature.LEGACY_SSE}},
)

V2025_06_18 = _evolve(
    V2025_03_26,
    "2025-06-18",
    add={
        "server_requests": {"elicitation/create"},
        "client_capabilities": {"elicitation"},
        "content_types": {"resource_link"},
        "features": {
            Feature.PROTOCOL_VERSION_HEADER,
            Feature.PROTECTED_RESOURCE_METADATA,
            Feature.STRUCTURED_OUTPUT,
            Feature.TITLES,
            Feature.RESOURCE_LINKS,
            Feature.META_FIELDS,
            Feature.ELICITATION,
            Feature.COMPLETION_CONTEXT,
        },
    },
    remove={"features": {Feature.BATCHING}},
)

V2025_11_25 = _evolve(
    V2025_06_18,
    "2025-11-25",
    add={
        "client_requests": _TASKS,
        "server_requests": _TASKS,
        "client_notifications": {"notifications/tasks/status"},
        "server_notifications": {
            "notifications/tasks/status",
            "notifications/elicitation/complete",
        },
        "client_capabilities": {"tasks"},
        "server_capabilities": {"tasks"},
        "content_types": {"tool_use", "tool_result"},
        "features": {
            Feature.SSE_POLLING,
            Feature.ICONS,
            Feature.IMPLEMENTATION_DETAILS,
            Feature.TOOL_NAME_RULES,
            Feature.ELICITATION_URL,
            Feature.ELICITATION_COMPLETE,
            Feature.TASKS_CORE,
            Feature.SAMPLING_TOOLS,
            Feature.JSON_SCHEMA_2020_12,
        },
        "deprecated": {'sampling includeContext "thisServer"/"allServers"'},
        "error_codes": {URL_ELICITATION_REQUIRED},
    },
)

V2026_07_28 = _evolve(
    V2025_11_25,
    "2026-07-28",
    era="modern",
    add={
        "client_requests": {"server/discover", "subscriptions/listen"},
        "server_notifications": {"notifications/subscriptions/acknowledged"},
        "client_capabilities": {"extensions"},
        "server_capabilities": {"extensions"},
        "features": {
            Feature.METHOD_HEADERS,
            Feature.PER_REQUEST_META,
            Feature.SERVER_DISCOVER,
            Feature.MRTR,
            Feature.RESULT_TYPE,
            Feature.CACHEABLE_RESULTS,
            Feature.SUBSCRIPTIONS_LISTEN,
            Feature.EXTENSIONS,
            Feature.OTEL_META,
        },
        "deprecated": {"roots", "sampling", "logging", "dynamic client registration"},
        "error_codes": {
            HEADER_MISMATCH,
            MISSING_REQUIRED_CLIENT_CAPABILITY,
            UNSUPPORTED_PROTOCOL_VERSION,
        },
    },
    remove={
        "client_requests": {
            "initialize",
            "ping",
            "logging/setLevel",
            "resources/subscribe",
            "resources/unsubscribe",
        }
        | _TASKS,
        "server_requests": {"ping"} | _TASKS,
        "client_notifications": {
            "notifications/initialized",
            "notifications/roots/list_changed",
            "notifications/tasks/status",
        },
        "server_notifications": {
            "notifications/tasks/status",
            "notifications/elicitation/complete",
        },
        "client_capabilities": {"tasks"},
        "server_capabilities": {"tasks"},
        "features": {
            Feature.HANDSHAKE,
            Feature.SESSIONS,
            Feature.GET_STREAM,
            Feature.SSE_RESUME,
            Feature.SSE_POLLING,
            Feature.TASKS_CORE,
            Feature.ELICITATION_COMPLETE,
        },
        "error_codes": {RESOURCE_NOT_FOUND_LEGACY},
    },
)

SPECS: dict[str, SpecVersion] = {
    v.name: v for v in (V2024_11_05, V2025_03_26, V2025_06_18, V2025_11_25, V2026_07_28)
}
KNOWN_VERSIONS: tuple[str, ...] = tuple(SPECS)
HANDSHAKE_VERSIONS: tuple[str, ...] = tuple(
    v for v, s in SPECS.items() if s.era == "handshake"
)
MODERN_VERSIONS: tuple[str, ...] = tuple(v for v, s in SPECS.items() if s.era == "modern")
LATEST = KNOWN_VERSIONS[-1]
LATEST_HANDSHAKE = HANDSHAKE_VERSIONS[-1]
LATEST_MODERN = MODERN_VERSIONS[-1]

# Version strings seen in the wild that map onto a released revision.
# 2024-10-07 predates 2024-11-05 and is still listed by the TypeScript SDK.
ALIASES: dict[str, str] = {"2024-10-07": "2024-11-05"}


def canonical(version: str) -> str:
    return ALIASES.get(version, version)


def get(version: str | None) -> SpecVersion | None:
    if version is None:
        return None
    return SPECS.get(canonical(version))


def spec_for(version: str | None) -> SpecVersion:
    """The registry entry for a version, falling back to the nearest known one.

    Unknown versions are compared as dates: a future revision is treated as
    the latest we know, an ancient one as the oldest.
    """
    if version is not None:
        known = get(version)
        if known is not None:
            return known
        older = [v for v in KNOWN_VERSIONS if v <= version]
        if older:
            return SPECS[older[-1]]
        return SPECS[KNOWN_VERSIONS[0]]
    return SPECS[LATEST]


def is_known(version: str) -> bool:
    return get(version) is not None


def era_of(version: str) -> Era:
    return spec_for(version).era


def introduced_method(method: str) -> str | None:
    """The first revision that defines a method, or None if none does."""
    for v, spec in SPECS.items():
        if spec.defines_method(method):
            return v
    return None


def introduced_feature(feature: Feature) -> str | None:
    for v, spec in SPECS.items():
        if spec.has(feature):
            return v
    return None


def introduced_capability(side: Literal["client", "server"], key: str) -> str | None:
    for v, spec in SPECS.items():
        caps = spec.client_capabilities if side == "client" else spec.server_capabilities
        if key in caps:
            return v
    return None


def introduced_content_type(ctype: str) -> str | None:
    for v, spec in SPECS.items():
        if ctype in spec.content_types:
            return v
    return None


def removed_method(method: str) -> str | None:
    """The first revision that dropped a method once defined, or None."""
    seen = False
    for v, spec in SPECS.items():
        if spec.defines_method(method):
            seen = True
        elif seen:
            return v
    return None
