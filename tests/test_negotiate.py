# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for era/version negotiation and the 2026-07-28 stateless protocol."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from mcp_test_driver.negotiate import ConnectOptions, ModernRejected, connect
from mcp_test_driver.protocol import META_PROTOCOL_VERSION, McpSession
from mcp_test_driver.repl import Repl
from mcp_test_driver.transport import HttpTransport, StdioTransport
from mcp_test_driver.transport.params import analyse, param_headers
from tests.conftest import SAMPLE_TOOLS, read_log
from tests.test_interactive import Scripted

MODERN = {"mode": "modern", "versions": ["2026-07-28"], "tools": SAMPLE_TOOLS}
DUAL = {"mode": "dual", "versions": ["2025-06-18", "2025-11-25", "2026-07-28"], "tools": SAMPLE_TOOLS}
LEGACY = {"mode": "legacy", "versions": ["2025-11-25"], "tools": SAMPLE_TOOLS}

Factory = Callable[..., McpSession]


@pytest.fixture
def stdio_session(fake_stdio, log_path: Path) -> Iterator[Factory]:  # noqa: ANN001
    made: list[McpSession] = []

    def make(scenario: dict[str, Any], **opts: Any) -> McpSession:
        t = StdioTransport(fake_stdio({"log_path": str(log_path), **scenario}), timeout=10)
        t.trace = False
        s = McpSession(t)
        made.append(s)
        connect(s, ConnectOptions(**{"probe_timeout": 1.0, **opts}))
        return s

    yield make
    for s in made:
        s.transport.close()


def _codes(s: McpSession) -> set[str]:
    return {f.code for f in s.findings}


class TestStdioEras:
    def test_modern_server(self, stdio_session: Factory, log_path: Path) -> None:
        s = stdio_session(MODERN)
        assert s.state.era == "modern" and s.state.version == "2026-07-28"
        assert s.state.supported == ["2026-07-28"]
        assert s.server_info["name"] == "fake-server"
        assert "server/discover: requested 2026-07-28" in s.state.how_chosen[0]
        assert [t["name"] for t in s.list_tools()] == ["echo"]
        sent = [m for m in read_log(log_path) if m.get("method") == "tools/list"][0]
        assert sent["params"]["_meta"][META_PROTOCOL_VERSION] == "2026-07-28"
        assert not any(m.get("method") == "initialize" for m in read_log(log_path))
        assert not _codes(s) - {"no-server-info-meta"}

    def test_dual_server_auto_and_legacy(self, stdio_session: Factory) -> None:
        assert stdio_session(DUAL).state.era == "modern"
        s = stdio_session(DUAL, era="legacy")
        assert s.state.era == "handshake" and s.state.version == "2025-11-25"

    def test_legacy_error_falls_back(self, stdio_session: Factory) -> None:
        s = stdio_session(LEGACY)
        assert s.state.era == "handshake" and s.state.version == "2025-11-25"
        assert any("falling back to initialize" in h for h in s.state.how_chosen)

    def test_legacy_silent_falls_back_after_timeout(self, stdio_session: Factory) -> None:
        s = stdio_session({**LEGACY, "on_unknown_pre_init": "silent"}, probe_timeout=0.3)
        assert s.state.era == "handshake"
        assert any("no reply" in h for h in s.state.how_chosen)

    def test_legacy_exit_is_restarted(self, stdio_session: Factory) -> None:
        s = stdio_session({**LEGACY, "on_unknown_pre_init": "exit"})
        assert s.state.era == "handshake" and s.state.initialized
        assert "exit-on-discover" in _codes(s)

    def test_unsupported_version_retry(self, stdio_session: Factory) -> None:
        s = stdio_session(MODERN, version="2099-01-01")
        assert s.state.version == "2026-07-28"
        assert any("2099-01-01 unsupported" in h for h in s.state.how_chosen)

    def test_era_modern_rejects_legacy_server(self, fake_stdio) -> None:  # noqa: ANN001
        t = StdioTransport(fake_stdio(LEGACY), timeout=10)
        t.trace = False
        with pytest.raises(ModernRejected):
            connect(McpSession(t), ConnectOptions(era="modern", probe_timeout=1.0))
        t.close()

    def test_removed_methods_noted_and_sent(self, stdio_session: Factory) -> None:
        s = stdio_session(MODERN)
        with pytest.raises(Exception, match="Method not found"):
            s.ping()
        assert "sent-request-outside-version" in _codes(s)

    def test_missing_result_type_and_cache_fields(self, stdio_session: Factory) -> None:
        s = stdio_session({**MODERN, "omit_result_type": True})
        s.list_tools()
        assert "missing-result-type" in _codes(s)
        assert "missing-cache-fields" not in _codes(s)  # the fake still sends ttlMs/cacheScope
        s._observe_result("tools/list", {"jsonrpc": "2.0", "id": 1, "result": {"tools": [], "resultType": "complete"}})
        assert "missing-cache-fields" in _codes(s)

    def test_legacy_resource_not_found_code_flagged(self, stdio_session: Factory) -> None:
        s = stdio_session(DUAL)
        s.state.era = "modern"
        s._observe_result("resources/read", {"jsonrpc": "2.0", "id": 9, "error": {"code": -32002, "message": "x"}})
        assert "legacy-resource-not-found-code" in _codes(s)


class TestMrtr:
    ELICIT = {"github": {"method": "elicitation/create", "params": {
        "message": "login?", "requestedSchema": {"type": "object", "properties": {"name": {"type": "string"}}}}}}

    def _scenario(self) -> dict[str, Any]:
        return {**MODERN, "tool_scripts": {"echo": [{"mrtr": self.ELICIT}, {"reply": {}}]}}

    def test_input_gathered_and_retried(self, stdio_session: Factory, log_path: Path) -> None:
        s = stdio_session(self._scenario())
        s.enable_elicitation(Scripted("a", "octocat"))
        resp = s.call_tool("echo", {"x": 1})
        answers = json.loads(resp["result"]["content"][0]["text"])
        assert answers[0]["inputResponses"] == {"github": {"action": "accept", "content": {"name": "octocat"}}}
        assert answers[0]["requestState"] == "state-1"
        calls = [m for m in read_log(log_path) if m.get("method") == "tools/call"]
        assert len(calls) == 2 and calls[0]["id"] != calls[1]["id"]
        assert calls[1]["params"]["arguments"] == {"x": 1}

    def test_missing_handler_returns_input_required(self, stdio_session: Factory) -> None:
        s = stdio_session(self._scenario())
        resp = s.call_tool("echo", {})
        assert resp["result"]["resultType"] == "input_required"


class TestListen:
    def test_modern_stdio(self, fake_stdio, log_path: Path, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
        event = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
        t = StdioTransport(fake_stdio({**MODERN, "log_path": str(log_path), "listen_events": [event]}), timeout=10)
        t.trace = False
        s = McpSession(t)
        connect(s, ConnectOptions())
        r = Repl(s)
        r.handle_line("/listen 0.5 tools")
        assert "acknowledged" in capsys.readouterr().out
        assert "tools" in r.cache.stale
        log = read_log(log_path)
        listen = [m for m in log if m.get("method") == "subscriptions/listen"][0]
        assert listen["params"]["notifications"] == {"toolsListChanged": True}
        cancels = [m for m in log if m.get("method") == "notifications/cancelled"]
        assert cancels[0]["params"]["requestId"] == listen["id"]
        assert "subscription-not-acknowledged" not in _codes(s)
        t.close()

    def test_modern_http(self, fake_http, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
        event = {"jsonrpc": "2.0", "method": "notifications/prompts/list_changed"}
        srv = fake_http({**MODERN, "http_mode": "sse", "listen_events": [event]})
        t = HttpTransport(srv.url, timeout=10)
        t.trace = False
        s = McpSession(t)
        connect(s, ConnectOptions())
        r = Repl(s)
        r.handle_line("/listen 1 prompts")
        assert "prompts" in r.cache.stale
        assert srv.cancelled
        t.close()

    def test_handshake_http_uses_get_stream(self, fake_http, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
        note = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
        srv = fake_http({**LEGACY, "session_id": "s", "get_events": [note]})
        t = HttpTransport(srv.url, timeout=10)
        t.trace = False
        s = McpSession(t)
        connect(s, ConnectOptions(era="legacy"))
        r = Repl(s)
        r.handle_line("/listen 0.5")
        assert "tools" in r.cache.stale
        assert not t.listening
        gets = [h for (m, _, h, _) in srv.requests if m == "GET"]
        assert gets[0]["Mcp-Session-Id"] == "s"
        t.close()

    def test_handshake_http_405(self, fake_http, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
        srv = fake_http({**LEGACY})
        t = HttpTransport(srv.url, timeout=10)
        t.trace = False
        s = McpSession(t)
        connect(s, ConnectOptions(era="legacy"))
        Repl(s).handle_line("/listen 0.2")
        assert "405" in capsys.readouterr().out
        t.close()

    def test_declined_types_reported(self, fake_stdio, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
        t = StdioTransport(fake_stdio({**MODERN, "listen_support": ["toolsListChanged"]}), timeout=10)
        t.trace = False
        s = McpSession(t)
        connect(s, ConnectOptions())
        Repl(s).handle_line("/listen 0.3 tools prompts")
        assert "declined: promptsListChanged" in capsys.readouterr().out
        t.close()


class TestHttpModern:
    def test_headers_and_no_session(self, fake_http) -> None:  # noqa: ANN001
        tools = [{"name": "sql", "inputSchema": {"type": "object", "properties": {
            "region": {"type": "string", "x-mcp-header": "Region"}, "q": {"type": "string"}}}}]
        srv = fake_http({**MODERN, "tools": tools, "session_id": None})
        t = HttpTransport(srv.url, timeout=10)
        t.trace = False
        s = McpSession(t)
        connect(s, ConnectOptions())
        assert s.state.era == "modern"
        r = Repl(s)
        r.handle_line("sql region=us-west1 q=select")
        h = srv.headers_for("tools/call")[0]
        assert h["MCP-Protocol-Version"] == "2026-07-28"
        assert h["Mcp-Method"] == "tools/call" and h["Mcp-Name"] == "sql"
        assert h["Mcp-Param-Region"] == "us-west1"
        assert "Mcp-Session-Id" not in h
        d = srv.headers_for("server/discover")[0]
        assert d["Mcp-Method"] == "server/discover"
        t.close()

    def test_legacy_http_falls_back(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({**LEGACY, "session_id": "s1"})
        t = HttpTransport(srv.url, timeout=10)
        t.trace = False
        s = McpSession(t)
        connect(s, ConnectOptions())
        assert s.state.era == "handshake" and t.session_id == "s1"
        assert "Mcp-Method" not in srv.headers_for("initialize")[0]
        t.close()

    def test_modern_error_in_400_body_counts_as_modern(self, fake_http) -> None:  # noqa: ANN001
        body = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32022, "message": "no",
                                                       "data": {"supported": ["2026-07-28"], "requested": "2099-01-01"}}}
        srv = fake_http({**MODERN, "status_for": {"server/discover": [400, body, {}]}})
        t = HttpTransport(srv.url, timeout=10)
        t.trace = False
        s = McpSession(t)
        with pytest.raises(ModernRejected):
            connect(s, ConnectOptions(era="modern", version="2099-01-01"))
        assert any("2099-01-01 unsupported" in h for h in s.state.how_chosen)
        t.close()


class TestHeaderParams:
    def test_valid(self) -> None:
        schema = {"type": "object", "properties": {
            "a": {"type": "string", "x-mcp-header": "A"},
            "n": {"type": "object", "properties": {"b": {"type": "integer", "x-mcp-header": "B"}}},
            "c": {"type": "boolean", "x-mcp-header": "C"}}}
        hp = analyse(schema)
        assert hp.valid and hp.paths == {("a",): "A", ("n", "b"): "B", ("c",): "C"}
        assert param_headers(schema, {"a": "x y", "n": {"b": 5}, "c": False}) == {
            "Mcp-Param-A": "x y", "Mcp-Param-B": "5", "Mcp-Param-C": "false"}
        assert param_headers(schema, {"a": "日本"})["Mcp-Param-A"].startswith("=?base64?")
        assert param_headers(schema, {}) == {}

    @pytest.mark.parametrize("schema", [
        {"properties": {"a": {"type": "number", "x-mcp-header": "A"}}},
        {"properties": {"a": {"type": "string", "x-mcp-header": "bad name"}}},
        {"properties": {"a": {"type": "array", "items": {"type": "string", "x-mcp-header": "A"}}}},
        {"oneOf": [{"properties": {"a": {"type": "string", "x-mcp-header": "A"}}}]},
        {"properties": {"a": {"type": "string", "x-mcp-header": "X"}, "b": {"type": "string", "x-mcp-header": "x"}}},
    ])
    def test_invalid(self, schema: dict) -> None:
        assert not analyse(schema).valid

    def test_invalid_annotation_is_a_finding(self) -> None:
        from mcp_test_driver import checks, versions
        from mcp_test_driver.diagnostics import DiagnosticLog

        log = DiagnosticLog()
        checks.check_tools(log, versions.V2026_07_28, [{"name": "t", "inputSchema": {
            "type": "object", "properties": {"a": {"type": "number", "x-mcp-header": "A"}}}}])
        assert any(f.code == "tool-x-mcp-header" for f in log)
