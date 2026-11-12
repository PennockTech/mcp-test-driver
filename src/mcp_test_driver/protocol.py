# Copyright 2026 Phil Pennock — see LICENSE file.

"""MCP session: request/response matching, framing per revision, dispatch.

The session owns the receive loop.  It sends a request, then reads messages
until the matching response arrives, handling on the way:

  - notifications  → listeners (display, cache invalidation, diagnostics)
  - server requests → the handler registry, whose answer is sent back
  - stray responses → reported, never returned to the wrong caller

Nothing here refuses to send a method because the negotiated revision lacks
it: we send it and record a finding.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import __version__, messages, versions
from .color import cyan, dim, eprint, red, yellow
from .diagnostics import DiagnosticLog, Level
from .handlers import HandlerError, HandlerRegistry, PingHandler, RootsHandler
from .interactive import ElicitationHandler, Prompter, SamplingHandler
from .messages import JsonObject, JsonValue, Kind, as_obj, classify
from .negotiate import ConnectOptions
from .sanitize import safe_json, sanitize
from .transport import (
    HttpStatusError,
    ReceiveTimeout,
    Transport,
    TransportError,
)
from .versions import Era, Feature

# Safety limit to prevent infinite pagination loops from a rogue server.
_MAX_PAGES = 100

META_PREFIX = "io.modelcontextprotocol/"
META_PROTOCOL_VERSION = META_PREFIX + "protocolVersion"
META_CLIENT_INFO = META_PREFIX + "clientInfo"
META_CLIENT_CAPABILITIES = META_PREFIX + "clientCapabilities"
META_LOG_LEVEL = META_PREFIX + "logLevel"
META_SERVER_INFO = META_PREFIX + "serverInfo"
META_SUBSCRIPTION_ID = META_PREFIX + "subscriptionId"

# Requests that may carry a progress token usefully.
_PROGRESS_METHODS = {"tools/call", "prompts/get", "resources/read", "completion/complete"}
# 2026-07-28: the only requests a server may answer with InputRequiredResult.
MRTR_METHODS = {"tools/call", "prompts/get", "resources/read"}
# 2026-07-28: results that must carry ttlMs and cacheScope (CacheableResult).
CACHEABLE_METHODS = {"tools/list", "prompts/list", "resources/list", "resources/read", "resources/templates/list"}
_MAX_MRTR_ROUNDS = 8


def _client_version() -> str:
    try:
        from importlib.metadata import version

        return version("mcp-test-driver")
    except Exception:
        return __version__


class McpError(Exception):
    """The server answered with a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"MCP error {code}: {message}")


class RequestCancelled(Exception):
    """The user interrupted a request; notifications/cancelled was sent."""


def error_from(resp: JsonObject) -> McpError | None:
    err = resp.get("error")
    if not isinstance(err, dict):
        return None
    code = err.get("code", -1)
    return McpError(
        code=code if isinstance(code, int) else -1,
        message=sanitize(str(err.get("message", "Unknown error"))),
        data=err.get("data"),
    )


@dataclass
class ServerState:
    """What we know about the server and how we are talking to it."""

    era: Era = "handshake"
    # The revision in effect (negotiated, or chosen for modern requests).
    version: str | None = None
    # What we offered in initialize / per-request _meta.
    offered: str | None = None
    # From server/discover or an UnsupportedProtocolVersion error.
    supported: list[str] = field(default_factory=list)
    server_info: JsonObject = field(default_factory=dict)
    capabilities: JsonObject = field(default_factory=dict)
    instructions: str | None = None
    # How the transport/era/version were chosen, for the start-up report.
    how_chosen: list[str] = field(default_factory=list)
    initialized: bool = False
    # The capabilities we declared in initialize (handshake era).
    client_capabilities: JsonObject = field(default_factory=dict)

    @property
    def spec(self) -> versions.SpecVersion:
        return versions.spec_for(self.version)


NotificationListener = Callable[[JsonObject], None]


class McpSession:
    """One logical connection to an MCP server, over any transport."""

    CLIENT_NAME = "mcp-test-driver"
    CLIENT_VERSION = _client_version()

    def __init__(
        self,
        transport: Transport,
        *,
        offer_version: str = versions.LATEST_HANDSHAKE,
    ) -> None:
        self.transport = transport
        self.offer_version = offer_version
        self.state = ServerState(offered=offer_version)
        self.findings = DiagnosticLog()
        self._id_seq = 0
        # IDs the user chose in /raw; ours skip them so none is ever reused.
        self._reserved: set[Any] = set()
        self._abandoned: set[Any] = set()
        self.handler_registry = HandlerRegistry()
        self.handler_registry.register("ping", PingHandler())
        self.notification_listeners: list[NotificationListener] = [print_notification]
        # Sticky user overrides from /meta.
        self.meta_overrides: JsonObject = {}
        # Modern era: per-request log level (replaces logging/setLevel).
        self.log_level: str | None = None
        self.progress = False
        self.otel = False
        self.extensions: dict[str, JsonObject] = {}
        # The challenge from the last 401/403, for OAuth discovery.
        self.last_www_authenticate: str | None = None
        # Methods of server-to-client requests we have answered.
        self.server_requests_seen: list[str] = []
        # How to (re)establish the protocol; set by negotiate.connect.
        self.connect_options = ConnectOptions()

    # ── convenience accessors ──────────────────────────────────────────

    @property
    def server_info(self) -> JsonObject:
        return self.state.server_info

    @property
    def server_capabilities(self) -> JsonObject:
        return self.state.capabilities

    @property
    def spec(self) -> versions.SpecVersion:
        return self.state.spec

    def client_info(self) -> JsonObject:
        return {"name": self.CLIENT_NAME, "version": self.CLIENT_VERSION}

    def client_capabilities(self) -> JsonObject:
        caps = self.handler_registry.capabilities()
        if self.extensions:
            caps["extensions"] = dict(self.extensions)
        return caps

    # ── framing ────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        # IDs are never reused within a process, even across reconnects, so a
        # late response from an old connection can never be mistaken for ours.
        self._id_seq += 1
        while self._id_seq in self._reserved:
            self._id_seq += 1
        return self._id_seq

    def reserve_ids(self, ids: list[Any]) -> None:
        self._reserved.update(i for i in ids if isinstance(i, (int, str)))

    def _request_meta(self, method: str) -> JsonObject:
        meta: JsonObject = {}
        if self.state.era == "modern" and method != "initialize":
            meta[META_PROTOCOL_VERSION] = self.state.version or self.offer_version
            meta[META_CLIENT_CAPABILITIES] = self.client_capabilities()
            meta[META_CLIENT_INFO] = self.client_info()
            if self.log_level:
                meta[META_LOG_LEVEL] = self.log_level
        if self.progress and method in _PROGRESS_METHODS:
            meta["progressToken"] = f"p{self._id_seq + 1}"
        if self.otel:
            trace_id = secrets.token_hex(16)
            span_id = secrets.token_hex(8)
            meta["traceparent"] = f"00-{trace_id}-{span_id}-01"
        meta.update(self.meta_overrides)
        return meta

    def build_request(self, method: str, params: JsonObject | None) -> JsonObject:
        params = dict(params) if params else {}
        meta = self._request_meta(method)
        if meta:
            merged = dict(meta)
            existing = params.get("_meta")
            if isinstance(existing, dict):
                merged.update(existing)
            params["_meta"] = merged
        req = messages.request(self._next_id(), method, params)
        traceparent = meta.get("traceparent")
        if traceparent:
            eprint(dim(f"  traceparent {traceparent}"))
        return req

    def _note_method(self, method: str, direction: str = "request") -> None:
        spec = self.spec
        if spec.defines_method(method):
            return
        intro = versions.introduced_method(method)
        removed = versions.removed_method(method)
        if intro is None:
            msg = f"{method} is not defined by any known revision; sent anyway"
        elif removed is not None and removed <= spec.name:
            msg = f"{method} was removed in {removed} (negotiated {spec.name}); sent anyway"
        else:
            msg = f"{method} is from {intro}, beyond negotiated {spec.name}; sent anyway"
        eprint(dim(f"  note: {msg}"))
        self.findings.note(f"sent-{direction}-outside-version", msg, shown=True)

    # ── the receive loop ───────────────────────────────────────────────

    def send_request(
        self,
        method: str,
        params: JsonObject | None = None,
        *,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> JsonObject:
        """Send a request and return the raw response message.

        If the server answers with an InputRequiredResult (multi round-trip
        requests, 2026-07-28), the inputs are gathered through the handler
        registry and the request is retried, as many rounds as it takes.
        """
        self._note_method(method)
        resp = self.exchange(self.build_request(method, params), timeout=timeout, headers=headers)
        for rounds in range(1, _MAX_MRTR_ROUNDS + 1):
            result = resp.get("result")
            if not (isinstance(result, dict) and result.get("resultType") == "input_required"):
                return resp
            retry = self._mrtr_params(method, params, result)
            if retry is None:
                return resp
            eprint(dim(f"  retrying {method} with the requested input (round {rounds})"))
            resp = self.exchange(self.build_request(method, retry), timeout=timeout, headers=headers)
        self.findings.violation(
            "mrtr-too-many-rounds",
            f"{method} still wanted input after {_MAX_MRTR_ROUNDS} rounds; stopped",
            "basic/patterns/mrtr",
            level=Level.WARNING,
        )
        return resp

    def _mrtr_params(self, method: str, params: JsonObject | None, result: JsonObject) -> JsonObject | None:
        """Fulfil an InputRequiredResult; the params for the retry, or None to stop."""
        if method not in MRTR_METHODS:
            self.findings.violation(
                "mrtr-wrong-method",
                f"{method} returned InputRequiredResult; only {', '.join(sorted(MRTR_METHODS))} may",
                "basic/patterns/mrtr#supported-requests",
            )
        if self.state.era != "modern":
            self.findings.beyond("mrtr-in-handshake-era", f"{method} used multi round-trip requests (2026-07-28)")
        requests = result.get("inputRequests")
        state = result.get("requestState")
        if requests is None and state is None:
            self.findings.violation(
                "mrtr-empty", "InputRequiredResult has neither inputRequests nor requestState", "basic/patterns/mrtr"
            )
            return None
        responses: JsonObject = {}
        for key, req in (requests.items() if isinstance(requests, dict) else []):
            inner = req if isinstance(req, dict) else {}
            inner_method = str(inner.get("method"))
            inner_params = as_obj(inner.get("params"))
            eprint(cyan(f"  <<< input requested: {sanitize(inner_method)} (key {sanitize(str(key))})"))
            self.server_requests_seen.append(inner_method)
            if not self.handler_registry.has(inner_method):
                eprint(red(f"  This client cannot answer {sanitize(inner_method)}; "
                           "enable it with /elicitation, /sampling or /roots and retry."))
                return None
            try:
                responses[str(key)] = self.handler_registry.dispatch(inner_method, inner_params)
            except HandlerError as e:
                eprint(yellow(f"  {sanitize(inner_method)} declined: {sanitize(e.message)}"))
                return None
        retry = dict(params or {})
        if responses:
            retry["inputResponses"] = responses
        if state is not None:
            retry["requestState"] = state
        return retry

    def exchange(
        self,
        req: JsonObject,
        *,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> JsonObject:
        """Send an already-built request; wait for its response."""
        req_id = req["id"]
        try:
            self.transport.send(req, headers=headers)
        except HttpStatusError as e:
            found = self._response_in(e.messages, req_id)
            if found is None:
                raise
            self.findings.note(
                "http-error-status-with-jsonrpc-body",
                f"HTTP {e.status} carried the JSON-RPC response to {req.get('method')}",
            )
            return found
        try:
            resp = self.wait_for(req_id, timeout=timeout)
            self._observe_result(str(req.get("method")), resp)
            return resp
        except KeyboardInterrupt:
            self.cancel(req_id, "interrupted by user")
            raise RequestCancelled(f"Request {req_id} cancelled") from None
        except ReceiveTimeout:
            self.cancel(req_id, "client timeout")
            raise

    def _response_in(self, msgs: list[JsonValue], req_id: Any) -> JsonObject | None:
        found = None
        for m in msgs:
            if classify(m) == Kind.RESPONSE and isinstance(m, dict) and m.get("id") == req_id:
                found = m
            else:
                self.handle_incoming(m)
        return found

    def wait_for(self, req_id: Any, *, timeout: float | None = None) -> JsonObject:
        while True:
            msg = self.transport.receive(timeout)
            if (
                classify(msg) == Kind.RESPONSE
                and isinstance(msg, dict)
                and msg.get("id") == req_id
            ):
                self._observe_response(msg)
                return msg
            self.handle_incoming(msg)

    def pump(self) -> None:
        """Handle anything that arrived while idle (between prompts)."""
        for msg in self.transport.poll():
            self.handle_incoming(msg)
        flush = getattr(self.transport, "flush_stderr", None)
        if flush is not None:
            flush()

    def handle_incoming(self, msg: JsonValue) -> None:
        kind = classify(msg)
        if kind == Kind.NOTIFICATION and isinstance(msg, dict):
            self._observe_notification(msg)
            for listener in self.notification_listeners:
                listener(msg)
        elif kind == Kind.REQUEST and isinstance(msg, dict):
            self._serve(msg)
        elif kind == Kind.RESPONSE and isinstance(msg, dict):
            rid = msg.get("id")
            if rid in self._abandoned:
                self._abandoned.discard(rid)
                eprint(dim(f"  (late response to cancelled request {sanitize(str(rid))} discarded)"))
            else:
                eprint(red(f"Warning: response id={sanitize(str(rid))} matches no outstanding request"))
                self.findings.violation(
                    "unmatched-response-id",
                    f"server sent a response with id {sanitize(str(rid))!r} that matches no request",
                    "basic#responses",
                    level=Level.WARNING,
                )
        else:
            eprint(red(f"Warning: invalid JSON-RPC message from server: {safe_json(msg, indent=None)[:200]}"))
            self.findings.violation("invalid-message", "server sent a message that is not valid JSON-RPC", "basic")

    def _observe_response(self, msg: JsonObject) -> None:
        if msg.get("jsonrpc") != "2.0":
            self.findings.violation("jsonrpc-version", 'response lacks "jsonrpc": "2.0"', "basic")

    def _observe_result(self, method: str, resp: JsonObject) -> None:
        """Conformance notes on a response, given the request that caused it."""
        modern = self.state.era == "modern"
        err = resp.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            if modern and code == versions.RESOURCE_NOT_FOUND_LEGACY:
                self.findings.violation(
                    "legacy-resource-not-found-code",
                    "server used -32002 for a missing resource; 2026-07-28 uses -32602",
                    "server/resources#error-handling",
                    level=Level.WARNING,
                )
            return
        result = resp.get("result")
        if not isinstance(result, dict):
            return
        meta = result.get("_meta")
        if isinstance(meta, dict) and isinstance(meta.get(META_SERVER_INFO), dict) and not self.state.server_info:
            self.state.server_info = meta[META_SERVER_INFO]
        if "resultType" in result and not modern:
            self.findings.beyond("result-type-in-handshake-era", f"{method} result carries resultType (2026-07-28)")
        if not modern:
            return
        if "resultType" not in result:
            self.findings.violation(
                "missing-result-type",
                f"{method} result lacks resultType, required in {self.spec.name}",
                "basic#responses",
                level=Level.WARNING,
            )
        if method in CACHEABLE_METHODS and result.get("resultType", "complete") == "complete":
            missing = [k for k in ("ttlMs", "cacheScope") if k not in result]
            if missing:
                self.findings.violation(
                    "missing-cache-fields",
                    f"{method} result lacks {' and '.join(missing)}, required in {self.spec.name}",
                    "basic/utilities/caching",
                    level=Level.WARNING,
                )
            scope = result.get("cacheScope")
            if scope is not None and scope not in ("public", "private"):
                self.findings.violation(
                    "bad-cache-scope", f"{method} cacheScope is {sanitize(str(scope))!r}", "basic/utilities/caching"
                )
        if not (isinstance(meta, dict) and META_SERVER_INFO in meta):
            self.findings.note("no-server-info-meta", "results do not carry io.modelcontextprotocol/serverInfo (SHOULD)")

    def _observe_notification(self, msg: JsonObject) -> None:
        method = str(msg.get("method"))
        if method.startswith("notifications/") and method not in self.spec.server_notifications:
            intro = versions.introduced_method(method)
            if intro is None:
                self.findings.note("unknown-notification", f"server sent unknown notification {sanitize(method)}")
            elif intro > self.spec.name:
                self.findings.beyond(
                    "notification-beyond-version",
                    f"server sent {method} (from {intro}) while claiming {self.spec.name}",
                )
            else:
                self.findings.violation(
                    "notification-not-in-version",
                    f"server sent {method}, which {self.spec.name} does not define",
                    level=Level.WARNING,
                )

    def _serve(self, msg: JsonObject) -> None:
        method = str(msg.get("method"))
        req_id = msg.get("id")
        self.server_requests_seen.append(method)
        eprint(cyan(f"  <<< server request: {sanitize(method)} (id={sanitize(str(req_id))})"))
        if self.state.era == "modern":
            self.findings.violation(
                "server-request-in-modern-era",
                f"server sent a JSON-RPC request ({sanitize(method)}); "
                f"{self.spec.name} requires multi round-trip results instead",
                "basic/patterns/mrtr",
            )
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}
        registry = self.handler_registry
        if registry.has(method):
            try:
                reply = messages.result(req_id, registry.dispatch(method, params))
            except HandlerError as e:
                reply = messages.error(req_id, e.code, e.message, e.data)
            except Exception as e:
                reply = messages.error(req_id, -32603, sanitize(str(e)))
        else:
            reply = messages.error(req_id, -32601, f"Method not supported: {method}")
        self.transport.send(reply)

    def cancel(self, req_id: Any, reason: str) -> None:
        self._abandoned.add(req_id)
        abandon = getattr(self.transport, "abandon_streams", None)
        if abandon is not None:
            abandon()
        try:
            self.notify("notifications/cancelled", {"requestId": req_id, "reason": reason})
        except TransportError as e:
            eprint(red(f"Could not send cancellation: {e}"))
        eprint(yellow(f"Cancelled request {req_id} ({reason})."))

    # ── calls ──────────────────────────────────────────────────────────

    def call(self, method: str, params: JsonObject | None = None, **kw: Any) -> JsonObject:
        """Send a request; return its result, or raise McpError."""
        resp = self.send_request(method, params, **kw)
        err = error_from(resp)
        if err is not None:
            raise err
        result = resp.get("result")
        if not isinstance(result, dict):
            self.findings.violation(
                "result-not-object", f"{method} result is not a JSON object", "basic#responses"
            )
            return {}
        return result

    def notify(self, method: str, params: JsonObject | None = None) -> None:
        self._note_method(method, "notification")
        self.transport.send(messages.notification(method, params))

    def send_raw(self, payload: Any) -> None:
        """Send arbitrary JSON as-is (for negative testing)."""
        self.transport.send(payload)

    def _paginated_list(self, method: str, result_key: str) -> list[JsonObject]:
        """Fetch a paginated list, following nextCursor across pages."""
        items: list[JsonObject] = []
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            params: JsonObject = {}
            if cursor is not None:
                params["cursor"] = cursor
            result = self.call(method, params)
            page = result.get(result_key, [])
            if isinstance(page, list):
                items.extend(p for p in page if isinstance(p, dict))
            nxt = result.get("nextCursor")
            cursor = nxt if isinstance(nxt, str) and nxt else None
            if cursor is None:
                break
        else:
            self.findings.violation(
                "pagination-runaway",
                f"{method} returned more than {_MAX_PAGES} pages; stopped",
                level=Level.WARNING,
            )
        return items

    # ── lifecycle ──────────────────────────────────────────────────────

    def apply_wire(self) -> None:
        """Point the transport's framing at the revision now in effect."""
        spec = self.spec
        wire = self.transport.wire
        wire.protocol_version = self.state.version
        wire.version_header = spec.has(Feature.PROTOCOL_VERSION_HEADER)
        wire.method_headers = spec.has(Feature.METHOD_HEADERS)
        wire.sessions = spec.has(Feature.SESSIONS)

    def initialize(self, version: str | None = None) -> JsonObject:
        """Handshake-era lifecycle: initialize, then notifications/initialized."""
        offer = version or self.offer_version
        self.state.era = "handshake"
        self.state.offered = offer
        # Until the server answers, frame as the version we offered.
        self.state.version = offer
        self.apply_wire()
        self.transport.wire.version_header = False  # the header follows negotiation
        caps = self.client_capabilities()
        self.state.client_capabilities = caps
        req = messages.request(
            self._next_id(),
            "initialize",
            {"protocolVersion": offer, "capabilities": caps, "clientInfo": self.client_info()},
        )
        resp = self.exchange(req)
        err = error_from(resp)
        if err is not None:
            raise err
        result = resp.get("result")
        if not isinstance(result, dict):
            raise ConnectionError("Server returned invalid initialize response")
        self._absorb_initialize(result, offer)
        self.notify("notifications/initialized")
        self.state.initialized = True
        return result

    def _absorb_initialize(self, result: JsonObject, offer: str) -> None:
        info = result.get("serverInfo")
        caps = result.get("capabilities")
        self.state.server_info = info if isinstance(info, dict) else {}
        self.state.capabilities = caps if isinstance(caps, dict) else {}
        instr = result.get("instructions")
        self.state.instructions = instr if isinstance(instr, str) else None
        if not isinstance(info, dict):
            self.findings.violation("missing-server-info", "initialize result lacks serverInfo", "basic/lifecycle")
        if not isinstance(caps, dict):
            self.findings.violation("missing-capabilities", "initialize result lacks capabilities", "basic/lifecycle")

        got = result.get("protocolVersion")
        if not isinstance(got, str) or not got:
            self.findings.violation(
                "missing-protocol-version",
                "initialize result lacks protocolVersion; assuming the offered version",
                "basic/lifecycle#version-negotiation",
            )
            got = offer
        canonical = versions.canonical(got)
        if canonical != got:
            self.findings.note("version-alias", f"server negotiated {got}, treated as {canonical}")
        self.state.version = canonical
        if got == offer:
            self.state.how_chosen.append(f"initialize: offered {offer}, server agreed")
        elif versions.is_known(got):
            self.state.how_chosen.append(f"initialize: offered {offer}, server counter-offered {got}")
            if got > offer:
                self.findings.violation(
                    "counter-offer-newer",
                    f"server answered with {got}, newer than the {offer} we offered",
                    "basic/lifecycle#version-negotiation",
                    level=Level.WARNING,
                )
        else:
            self.state.how_chosen.append(
                f"initialize: offered {offer}, server replied with unknown version {sanitize(got)}"
            )
            self.findings.violation(
                "unknown-protocol-version",
                f"server negotiated {sanitize(got)}, which this client does not know; "
                f"continuing as {versions.spec_for(got).name}",
                "basic/lifecycle#version-negotiation",
                level=Level.WARNING,
            )
        if versions.era_of(canonical) == "modern" and versions.is_known(canonical):
            self.findings.violation(
                "initialize-negotiated-modern",
                f"initialize negotiated {canonical}, a revision without the initialize handshake",
                "basic/versioning",
                level=Level.WARNING,
            )
        self.apply_wire()

    def adopt_modern(self, version: str, result: JsonObject, how: str) -> None:
        """Record a server/discover result: the stateless era is in effect."""
        supported = result.get("supportedVersions")
        self.state.era = "modern"
        self.state.supported = [v for v in supported if isinstance(v, str)] if isinstance(supported, list) else []
        if not self.state.supported:
            self.findings.violation("discover-no-versions", "server/discover result lacks supportedVersions",
                                    "schema#discoverresult")
        caps = result.get("capabilities")
        self.state.capabilities = caps if isinstance(caps, dict) else {}
        if not isinstance(caps, dict):
            self.findings.violation("discover-no-capabilities", "server/discover result lacks capabilities",
                                    "schema#discoverresult")
        instr = result.get("instructions")
        self.state.instructions = instr if isinstance(instr, str) else None
        meta = result.get("_meta")
        info = meta.get(META_SERVER_INFO) if isinstance(meta, dict) else None
        self.state.server_info = info if isinstance(info, dict) else {}
        self.state.version = version
        self.state.offered = version
        self.state.client_capabilities = self.client_capabilities()
        self.state.how_chosen.append(how)
        self.state.initialized = True
        self.apply_wire()

    def reset_to_handshake(self) -> None:
        """Forget a failed modern probe before falling back to initialize."""
        how = self.state.how_chosen
        self.state = ServerState(offered=self.offer_version, how_chosen=how)
        self.apply_wire()

    def reconnect(self) -> None:
        self.transport.reconnect()
        self._abandoned.clear()
        self.state = ServerState(offered=self.offer_version)
        self.findings.clear()

    # ── typed MCP methods ──────────────────────────────────────────────

    def list_tools(self) -> list[JsonObject]:
        return self._paginated_list("tools/list", "tools")

    def call_tool(self, name: str, arguments: JsonObject, **kw: Any) -> JsonObject:
        return self.send_request("tools/call", {"name": name, "arguments": arguments}, **kw)

    def list_resources(self) -> list[JsonObject]:
        return self._paginated_list("resources/list", "resources")

    def list_resource_templates(self) -> list[JsonObject]:
        return self._paginated_list("resources/templates/list", "resourceTemplates")

    def read_resource(self, uri: str) -> JsonObject:
        return self.send_request("resources/read", {"uri": uri})

    def subscribe_resource(self, uri: str) -> None:
        self.call("resources/subscribe", {"uri": uri})

    def unsubscribe_resource(self, uri: str) -> None:
        self.call("resources/unsubscribe", {"uri": uri})

    def list_prompts(self) -> list[JsonObject]:
        return self._paginated_list("prompts/list", "prompts")

    def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> JsonObject:
        params: JsonObject = {"name": name}
        if arguments:
            params["arguments"] = arguments
        return self.send_request("prompts/get", params)

    def ping(self) -> JsonObject:
        return self.call("ping")

    def set_log_level(self, level: str) -> JsonObject:
        return self.call("logging/setLevel", {"level": level})

    def complete(
        self,
        ref: JsonObject,
        argument_name: str,
        argument_value: str,
        context: dict[str, str] | None = None,
    ) -> JsonObject:
        """completion/complete.

        ref is {"type": "ref/prompt", "name": ...} or
        {"type": "ref/resource", "uri": ...}.
        """
        params: JsonObject = {
            "ref": ref,
            "argument": {"name": argument_name, "value": argument_value},
        }
        if context:
            params["context"] = {"arguments": context}
        return self.call("completion/complete", params)

    # ── client capabilities ────────────────────────────────────────────

    def enable_roots(self, base_path: Any) -> None:
        """Advertise base_path as a filesystem root (takes effect on the next
        initialize; announced at once with notifications/roots/list_changed
        when the session is already up)."""
        self.handler_registry.register("roots/list", RootsHandler(base_path))

    def disable_roots(self) -> None:
        self.handler_registry.unregister("roots/list")

    def enable_elicitation(self, prompter: Prompter, modes: frozenset[str] = frozenset({"form"})) -> None:
        self.handler_registry.register("elicitation/create", ElicitationHandler(prompter, modes))

    def enable_sampling(self, prompter: Prompter) -> None:
        self.handler_registry.register("sampling/createMessage", SamplingHandler(prompter))

    def disable(self, method: str) -> None:
        self.handler_registry.unregister(method)

    def roots_list_changed(self) -> bool:
        """Tell the server our roots changed, if the handshake promised we would."""
        roots = self.state.client_capabilities.get("roots")
        if not (self.state.initialized and isinstance(roots, dict) and roots.get("listChanged")):
            return False
        self.notify("notifications/roots/list_changed")
        return True

    @property
    def roots_handler(self) -> RootsHandler | None:
        handler = self.handler_registry.get("roots/list")
        return handler if isinstance(handler, RootsHandler) else None


# ── notification display ───────────────────────────────────────────────

_LOG_COLOURS = {
    "debug": dim,
    "info": cyan,
    "notice": cyan,
    "warning": yellow,
    "error": red,
    "critical": red,
    "alert": red,
    "emergency": red,
}


def print_notification(msg: JsonObject) -> None:
    """Default display for server notifications (stderr, sanitized)."""
    method = sanitize(str(msg.get("method", "?")))
    params = msg.get("params")
    p = params if isinstance(params, dict) else {}
    if method == "notifications/message":
        level = sanitize(str(p.get("level", "info")))
        logger = p.get("logger")
        data = p.get("data")
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        prefix = f"[server log {level}"
        if isinstance(logger, str):
            prefix += f" {sanitize(logger)}"
        colour = _LOG_COLOURS.get(level, cyan)
        eprint(colour(f"  {prefix}] {sanitize(text)}"))
        return
    if method == "notifications/progress":
        token = sanitize(str(p.get("progressToken", "?")))
        progress = p.get("progress", "?")
        total = p.get("total")
        line = f"  [progress {token}] {sanitize(str(progress))}"
        if total is not None:
            line += f"/{sanitize(str(total))}"
        message = p.get("message")
        if isinstance(message, str):
            line += f"  {sanitize(message)}"
        eprint(cyan(line))
        return
    if method == "notifications/elicitation/complete":
        eprint(cyan(f"  *** elicitation {sanitize(str(p.get('elicitationId', '?')))} completed out of band"))
        return
    if params:
        eprint(cyan(f"  *** notification: {method} {safe_json(params, indent=None)}"))
    else:
        eprint(cyan(f"  *** notification: {method}"))
