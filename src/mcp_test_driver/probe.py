# Copyright 2026 Phil Pennock — see LICENSE file.

"""Active probing and the server conformance check suite.

Probes send targeted requests to find out what a server really does,
whatever revision it claims: features from later revisions it supports
anyway, removed methods it still answers, and how it handles bad input.
Nothing here calls a server's own tools; the only tool call names a tool
that does not exist.

Check IDs match the official conformance suite's scenario names where the
same behaviour is tested (docs/specs/conformance/), so results can be
compared; ours are generic and run against any server, stdio included.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from . import versions
from .diagnostics import Level
from .messages import Kind, as_obj, classify
from .negotiate import ConnectOptions, connect
from .protocol import McpError
from .transport import (
    HttpStatusError,
    NoPendingMessages,
    ReceiveTimeout,
    TransportError,
)
from .versions import Feature

if TYPE_CHECKING:
    from .protocol import McpSession
    from .repl import SessionCache

PROBE_TIMEOUT = 5.0
_NO_SUCH_METHOD = "mcp-test-driver/no-such-method"
_NO_SUCH_TOOL = "mcp_test_driver_no_such_tool"
_NO_SUCH_URI = "mcp-test-driver://no-such-resource"


class Outcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    SKIP = "skip"
    ERROR = "error"
    # Answered with an error that says neither "no such method" nor "bad params".
    UNCLEAR = "unclear"


@dataclass
class Result:
    id: str
    title: str
    outcome: Outcome
    detail: str = ""
    # The revision that defines what was probed, for the claimed/observed matrix.
    defined_in: str | None = None
    # Whether the server claims it: the negotiated revision defines it and,
    # for optional features, the capability is advertised.
    claimed: bool | None = None
    # Whether the negotiated revision defines it at all.
    in_revision: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, StrEnum) else v) for k, v in self.__dict__.items()}


@dataclass
class Context:
    session: McpSession
    lists: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def spec(self) -> versions.SpecVersion:
        return self.session.spec

    @property
    def http(self) -> bool:
        return self.session.transport.kind in ("http", "sse")


Probe = Callable[[Context], Result]


def _call(ctx: Context, method: str, params: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    """The response, with "_http_status" set on HTTP; synthetic errors for transport failures."""
    try:
        resp = ctx.session.send_request(method, params, timeout=kw.pop("timeout", PROBE_TIMEOUT), **kw)
        status = getattr(ctx.session.transport, "last_status", None)
        return dict(resp, _http_status=status) if status is not None else resp
    except HttpStatusError as e:
        for m in e.messages:
            if classify(m) == Kind.RESPONSE:
                return as_obj(m) | {"_http_status": e.status}
        return {"error": {"code": None, "message": f"HTTP {e.status}"}, "_http_status": e.status}
    except ReceiveTimeout:
        return {"error": {"code": None, "message": "no reply"}, "_timeout": True}
    except TransportError as e:
        return {"error": {"code": None, "message": str(e)}}


def _advertised(caps: dict[str, Any], cap: tuple[str, ...]) -> bool:
    node: Any = caps
    for key in cap:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return node is not False


def _method_probe(method: str, params: dict[str, Any] | None = None, cap: tuple[str, ...] = ()) -> Probe:
    """Does the server implement a method?

    A result, or -32602 (the method exists but disliked our params), means
    yes; -32601 or silence means no; any other error is unclear (e.g. "not
    initialized" from a legacy server sent a stateless-era method).
    "Claimed" means the negotiated revision defines the method and, for
    optional features, the server advertised the capability (`cap` path).
    """

    def run(ctx: Context) -> Result:
        resp = _call(ctx, method, params)
        err = as_obj(resp.get("error"))
        defined = versions.introduced_method(method)
        advertised = not cap or _advertised(ctx.session.server_capabilities, cap)
        claimed = ctx.spec.defines_method(method) and advertised
        rid, title = f"method:{method}", method
        if not err:
            outcome, detail = Outcome.SUPPORTED, "answered"
        elif err.get("code") == -32602:
            outcome, detail = Outcome.SUPPORTED, "exists (rejected our params)"
        elif err.get("code") == -32601:
            outcome, detail = Outcome.UNSUPPORTED, "method not found"
        elif resp.get("_timeout"):
            outcome, detail = Outcome.UNSUPPORTED, "no reply"
        else:
            outcome, detail = Outcome.UNCLEAR, f"error {err.get('code')}: {err.get('message')}"
        if outcome == Outcome.SUPPORTED and cap and not advertised and ctx.spec.defines_method(method):
            ctx.session.findings.note(f"probe-{rid}-unadvertised",
                                      f"{method} works although the {'.'.join(cap)!r} capability is not advertised")
        return Result(rid, title, outcome, detail, defined, claimed, ctx.spec.defines_method(method))

    return run


# ── capability / feature probes ────────────────────────────────────────


def probe_get_stream(ctx: Context) -> Result:
    t = ctx.session.transport
    claimed = ctx.spec.has(Feature.GET_STREAM)
    intro = versions.introduced_feature(Feature.GET_STREAM)
    open_stream = getattr(t, "open_listen_stream", None)
    if t.kind != "http" or open_stream is None:
        return Result("feature:get-stream", "HTTP GET listening stream", Outcome.SKIP, "not Streamable HTTP", intro, claimed)
    try:
        open_stream()
    except HttpStatusError as e:
        return Result("feature:get-stream", "HTTP GET listening stream", Outcome.UNSUPPORTED, f"HTTP {e.status}", intro, claimed)
    except TransportError as e:
        return Result("feature:get-stream", "HTTP GET listening stream", Outcome.ERROR, str(e), intro, claimed)
    getattr(t, "close_listen_stream")()
    return Result("feature:get-stream", "HTTP GET listening stream", Outcome.SUPPORTED, "SSE stream opened", intro, claimed)


def probe_batch(ctx: Context) -> Result:
    intro = versions.introduced_feature(Feature.BATCHING)
    claimed = ctx.spec.has(Feature.BATCHING)
    s = ctx.session
    ids = [s._next_id(), s._next_id()]
    batch = [s.build_request("ping", None) | {"id": i} for i in ids]
    try:
        s.send_raw(batch)
    except HttpStatusError as e:
        return Result("feature:batch", "JSON-RPC batch", Outcome.UNSUPPORTED, f"HTTP {e.status}", intro, claimed)
    except TransportError as e:
        return Result("feature:batch", "JSON-RPC batch", Outcome.ERROR, str(e), intro, claimed)
    got: list[Any] = []
    deadline = time.monotonic() + PROBE_TIMEOUT
    while time.monotonic() < deadline and len(got) < 2:
        try:
            msg = s.transport.receive(timeout=max(deadline - time.monotonic(), 0.1))
        except (ReceiveTimeout, NoPendingMessages):
            break
        if classify(msg) == Kind.RESPONSE:
            got.append(msg)
        else:
            s.handle_incoming(msg)
    s._abandoned.update(i for i in ids if i not in {as_obj(m).get("id") for m in got})
    ok = sum(1 for m in got if as_obj(m).get("id") in ids and "result" in as_obj(m))
    if ok == 2:
        return Result("feature:batch", "JSON-RPC batch", Outcome.SUPPORTED, "both requests answered", intro, claimed)
    return Result("feature:batch", "JSON-RPC batch", Outcome.UNSUPPORTED,
                  f"{ok}/2 answered" if got else "no reply", intro, claimed)


def probe_version_header(ctx: Context) -> Result:
    """2025-06-18+: an unsupported MCP-Protocol-Version must get 400."""
    claimed = ctx.spec.has(Feature.PROTOCOL_VERSION_HEADER)
    intro = versions.introduced_feature(Feature.PROTOCOL_VERSION_HEADER)
    title = "rejects a bogus MCP-Protocol-Version"
    if ctx.session.transport.kind != "http":
        return Result("check:protocol-version-header", title, Outcome.SKIP, "not Streamable HTTP", intro, claimed)
    resp = _call(ctx, "tools/list", None, headers={"MCP-Protocol-Version": "1999-01-01"})
    status = resp.get("_http_status")
    if status == 400:
        code = as_obj(resp.get("error")).get("code")
        return Result("check:protocol-version-header", title, Outcome.PASS, f"HTTP 400 ({code})", intro, claimed)
    outcome = Outcome.FAIL if claimed else Outcome.WARN
    return Result("check:protocol-version-header", title, outcome,
                  f"accepted it (HTTP {status or 200})", intro, claimed)


def probe_method_header(ctx: Context) -> Result:
    """2026-07-28: Mcp-Method disagreeing with the body must get 400 HeaderMismatch."""
    claimed = ctx.spec.has(Feature.METHOD_HEADERS)
    intro = versions.introduced_feature(Feature.METHOD_HEADERS)
    title = "rejects a mismatched Mcp-Method header"
    if ctx.session.transport.kind != "http" or not claimed:
        return Result("check:http-header-validation", title, Outcome.SKIP, "needs 2026-07-28 over HTTP", intro, claimed)
    resp = _call(ctx, "tools/list", None, headers={"Mcp-Method": "prompts/list"})
    code = as_obj(resp.get("error")).get("code")
    if resp.get("_http_status") == 400 and code == versions.HEADER_MISMATCH:
        return Result("check:http-header-validation", title, Outcome.PASS, "400 HeaderMismatch", intro, claimed)
    return Result("check:http-header-validation", title, Outcome.FAIL,
                  f"got HTTP {resp.get('_http_status', 200)}, error {code}", intro, claimed)


def probe_origin(ctx: Context) -> Result:
    """Servers must validate Origin (DNS rebinding); 2025-11-25 says 403."""
    title = "rejects a foreign Origin (DNS rebinding)"
    if ctx.session.transport.kind != "http":
        return Result("dns-rebinding-protection", title, Outcome.SKIP, "not Streamable HTTP")
    resp = _call(ctx, "tools/list", None, headers={"Origin": "http://evil.example"})
    status = resp.get("_http_status")
    if status == 403:
        return Result("dns-rebinding-protection", title, Outcome.PASS, "HTTP 403")
    if status is not None and status >= 400:
        return Result("dns-rebinding-protection", title, Outcome.WARN, f"HTTP {status} (2025-11-25 says 403)")
    return Result("dns-rebinding-protection", title, Outcome.FAIL, "accepted a request from http://evil.example")


# ── error-handling checks ──────────────────────────────────────────────


def check_unknown_method(ctx: Context) -> Result:
    resp = _call(ctx, _NO_SUCH_METHOD)
    code = as_obj(resp.get("error")).get("code")
    title = "unknown method gets -32601"
    if code == -32601:
        return Result("check:unknown-method", title, Outcome.PASS)
    return Result("check:unknown-method", title, Outcome.FAIL, f"got {code if resp.get('error') else 'a result'}")


def check_unknown_tool(ctx: Context) -> Result:
    resp = _call(ctx, "tools/call", {"name": _NO_SUCH_TOOL, "arguments": {}})
    err = as_obj(resp.get("error"))
    result = as_obj(resp.get("result"))
    title = "calling an unknown tool is an error"
    if err.get("code") == -32602:
        return Result("check:unknown-tool", title, Outcome.PASS, "-32602")
    if err:
        return Result("check:unknown-tool", title, Outcome.WARN, f"error {err.get('code')} (spec: -32602)")
    if result.get("isError"):
        return Result("check:unknown-tool", title, Outcome.WARN, "isError result (spec: protocol error -32602)")
    return Result("check:unknown-tool", title, Outcome.FAIL, "succeeded")


def check_resource_not_found(ctx: Context) -> Result:
    resp = _call(ctx, "resources/read", {"uri": _NO_SUCH_URI})
    code = as_obj(resp.get("error")).get("code")
    title = "missing resource gets the right error"
    want = -32602 if ctx.spec.era == "modern" else versions.RESOURCE_NOT_FOUND_LEGACY
    if code == want:
        return Result("sep-2164-resource-not-found", title, Outcome.PASS, str(code))
    if code in (-32602, versions.RESOURCE_NOT_FOUND_LEGACY):
        return Result("sep-2164-resource-not-found", title, Outcome.WARN, f"{code}; {ctx.spec.name} expects {want}")
    return Result("sep-2164-resource-not-found", title, Outcome.FAIL, f"got {code if resp.get('error') else 'a result'}")


def check_parse_error(ctx: Context) -> Result:
    title = "malformed JSON gets -32700"
    send_bytes = getattr(ctx.session.transport, "send_bytes", None)
    if send_bytes is None:
        return Result("check:parse-error", title, Outcome.SKIP)
    try:
        send_bytes(b'{"jsonrpc": "2.0", "id": ')
    except HttpStatusError as e:
        codes = [as_obj(as_obj(m).get("error")).get("code") for m in e.messages]
        if -32700 in codes:
            return Result("check:parse-error", title, Outcome.PASS, f"HTTP {e.status} with -32700")
        return Result("check:parse-error", title, Outcome.WARN, f"HTTP {e.status} without a JSON-RPC error")
    except TransportError as e:
        return Result("check:parse-error", title, Outcome.ERROR, str(e))
    try:
        msg = ctx.session.transport.receive(timeout=PROBE_TIMEOUT)
    except (ReceiveTimeout, NoPendingMessages):
        return Result("check:parse-error", title, Outcome.WARN, "no reply")
    code = as_obj(as_obj(msg).get("error")).get("code")
    return Result("check:parse-error", title, Outcome.PASS if code == -32700 else Outcome.FAIL, f"got {code}")


# ── list checks (official scenario names) ──────────────────────────────


def _list_check(cid: str, kind: str, cap: str) -> Probe:
    def run(ctx: Context) -> Result:
        title = f"{kind} list"
        if cap not in ctx.session.server_capabilities and kind != "tools":
            return Result(cid, title, Outcome.SKIP, f"{cap!r} not advertised")
        if kind not in ctx.lists:
            return Result(cid, title, Outcome.SKIP, "not fetched")
        bad = [f for f in ctx.session.findings if f.level >= Level.ERROR and f.code.startswith(kind.rstrip("s"))]
        n = len(ctx.lists[kind])
        if bad:
            return Result(cid, title, Outcome.FAIL, f"{n} listed; {len(bad)} error finding(s)")
        return Result(cid, title, Outcome.PASS, f"{n} listed")

    return run


def check_caching(ctx: Context) -> Result:
    title = "list results carry ttlMs and cacheScope"
    if ctx.spec.era != "modern":
        return Result("caching", title, Outcome.SKIP, "handshake era")
    missing = [f for f in ctx.session.findings if f.code == "missing-cache-fields"]
    return Result("caching", title, Outcome.FAIL if missing else Outcome.PASS,
                  missing[0].message if missing else "")


def check_completion(ctx: Context) -> Result:
    title = "completion/complete"
    if "completions" not in ctx.session.server_capabilities:
        return Result("completion-complete", title, Outcome.SKIP, "'completions' not advertised")
    prompts = [p for p in ctx.lists.get("prompts", []) if as_obj(p).get("arguments")]
    if not prompts:
        return Result("completion-complete", title, Outcome.SKIP, "no prompt with arguments")
    p = as_obj(prompts[0])
    arg = as_obj(p["arguments"][0])
    try:
        result = ctx.session.complete({"type": "ref/prompt", "name": p.get("name")}, str(arg.get("name")), "")
    except (McpError, TransportError) as e:
        return Result("completion-complete", title, Outcome.FAIL, str(e))
    values = as_obj(result.get("completion")).get("values")
    if not isinstance(values, list):
        return Result("completion-complete", title, Outcome.FAIL, "no completion.values")
    return Result("completion-complete", title, Outcome.PASS, f"{len(values)} value(s)")


# ── suites ─────────────────────────────────────────────────────────────

PROBES: list[Probe] = [
    _method_probe("server/discover"),
    _method_probe("ping"),
    _method_probe("logging/setLevel", {"level": "info"}, cap=("logging",)),
    _method_probe("completion/complete", {"ref": {"type": "ref/prompt", "name": "x"},
                                          "argument": {"name": "x", "value": ""}}, cap=("completions",)),
    _method_probe("tasks/list", cap=("tasks", "list")),
    _method_probe("resources/subscribe", {"uri": _NO_SUCH_URI}, cap=("resources", "subscribe")),
    probe_get_stream,
    probe_batch,
]

CHECKS: list[Probe] = [
    _list_check("tools-list", "tools", "tools"),
    _list_check("resources-list", "resources", "resources"),
    _list_check("prompts-list", "prompts", "prompts"),
    check_completion,
    check_caching,
    check_unknown_method,
    check_unknown_tool,
    check_resource_not_found,
    check_parse_error,
    probe_version_header,
    probe_method_header,
    probe_origin,
]


def run(probes: list[Probe], ctx: Context) -> list[Result]:
    results = []
    for p in probes:
        try:
            results.append(p(ctx))
        except (McpError, TransportError) as e:
            results.append(Result(getattr(p, "__name__", "probe"), "probe", Outcome.ERROR, str(e)))
    record(ctx, results)
    return results


def record(ctx: Context, results: list[Result]) -> None:
    """Turn notable results into findings."""
    log = ctx.session.findings
    for r in results:
        in_revision = r.claimed if r.in_revision is None else r.in_revision
        if r.outcome == Outcome.SUPPORTED and in_revision is False and r.defined_in:
            if r.defined_in > ctx.spec.name:
                log.beyond(f"probe-{r.id}", f"{r.title} works, though it is from {r.defined_in}")
            else:
                log.note(f"probe-{r.id}", f"{r.title} still works, though {ctx.spec.name} dropped it")
        elif r.outcome == Outcome.UNSUPPORTED and r.claimed:
            log.violation(f"probe-{r.id}", f"{r.title} is part of {ctx.spec.name} but {r.detail}",
                          level=Level.WARNING)
        elif r.outcome == Outcome.FAIL:
            log.violation(f"check-{r.id}", f"{r.title}: {r.detail}", level=Level.ERROR)
        elif r.outcome == Outcome.WARN:
            log.violation(f"check-{r.id}", f"{r.title}: {r.detail}", level=Level.WARNING)


def probe_versions(session: McpSession) -> list[tuple[str, str]]:
    """Reconnect offering each known revision; report what the server answers."""
    original = session.connect_options
    rows: list[tuple[str, str]] = []
    for v in versions.KNOWN_VERSIONS:
        era = versions.era_of(v)
        session.reconnect()
        try:
            connect(session, ConnectOptions(era="modern" if era == "modern" else "legacy", version=v,
                                            probe_timeout=PROBE_TIMEOUT))
            got = session.state.version or "?"
            via = "stateless" if session.state.era == "modern" else "initialize"
            rows.append((v, f"{got} via {via}" + ("" if got == v else "  (counter-offer)")))
        except (McpError, TransportError, ConnectionError) as e:
            rows.append((v, f"refused: {e}"))
    session.reconnect()
    connect(session, original)
    return rows


def matrix_rows(results: list[Result]) -> list[tuple[str, str, str, str]]:
    """(feature, defined in, claimed, observed) for the probed features."""
    rows = []
    for r in results:
        if r.claimed is None:
            continue
        claimed = "yes" if r.claimed else "no"
        observed = {Outcome.SUPPORTED: "yes", Outcome.UNSUPPORTED: "no"}.get(r.outcome, str(r.outcome))
        rows.append((r.title, r.defined_in or "?", claimed, observed))
    return rows


def context(session: McpSession, cache: SessionCache | None) -> Context:
    return Context(session, dict(cache.lists) if cache is not None else {})


def summary(results: list[Result]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in results:
        out[r.outcome] = out.get(r.outcome, 0) + 1
    return out
