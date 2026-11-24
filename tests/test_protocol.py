# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for the MCP session layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_test_driver import versions
from mcp_test_driver.protocol import (
    META_CLIENT_CAPABILITIES,
    META_CLIENT_INFO,
    META_LOG_LEVEL,
    META_PROTOCOL_VERSION,
    McpError,
    McpSession,
    error_from,
)
from mcp_test_driver.transport import HttpTransport, ReceiveTimeout, StdioTransport
from tests.conftest import SAMPLE_TOOLS, read_log


def _session(cmd: list[str], **kw) -> McpSession:  # noqa: ANN003
    t = StdioTransport(cmd, timeout=10)
    t.trace = False
    return McpSession(t, **kw)


def _codes(s: McpSession) -> set[str]:
    return {f.code for f in s.findings}


@pytest.fixture
def session_factory(fake_stdio, log_path: Path):  # noqa: ANN001, ANN201
    made: list[McpSession] = []

    def make(scenario: dict, **kw) -> McpSession:  # noqa: ANN003
        scenario = {"log_path": str(log_path), "tools": SAMPLE_TOOLS, **scenario}
        s = _session(fake_stdio(scenario), **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.transport.close()


class TestInitialize:
    def test_offers_latest_handshake_and_sends_initialized(self, session_factory, log_path: Path) -> None:  # noqa: ANN001
        s = session_factory({"versions": ["2025-11-25"]})
        s.initialize()
        log = read_log(log_path)
        assert log[0]["method"] == "initialize"
        assert log[0]["params"]["protocolVersion"] == versions.LATEST_HANDSHAKE
        assert log[0]["params"]["clientInfo"]["name"] == "mcp-test-driver"
        assert _wait_log(log_path, "notifications/initialized")
        assert s.state.version == "2025-11-25" and s.state.initialized

    def test_server_counter_offer_accepted(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({"versions": ["2024-11-05", "2025-03-26"]})
        s.initialize()
        assert s.state.version == "2025-03-26"
        assert "counter-offered 2025-03-26" in s.state.how_chosen[0]
        assert not s.transport.wire.version_header

    def test_explicit_offer(self, session_factory, log_path: Path) -> None:  # noqa: ANN001
        s = session_factory({"versions": ["2024-11-05", "2025-06-18"]})
        s.initialize("2024-11-05")
        assert read_log(log_path)[0]["params"]["protocolVersion"] == "2024-11-05"
        assert s.state.version == "2024-11-05"

    def test_newer_counter_offer_flagged(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({"counter_offer": "2025-11-25"})
        s.initialize("2025-03-26")
        assert "counter-offer-newer" in _codes(s)

    def test_unknown_version_flagged_and_mapped(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({"counter_offer": "2025-09-01"})
        s.initialize()
        assert "unknown-protocol-version" in _codes(s)
        assert s.state.version == "2025-09-01"
        assert s.spec.name == "2025-06-18"

    def test_alias_version(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({"counter_offer": "2024-10-07"})
        s.initialize()
        assert s.state.version == "2024-11-05"
        assert "version-alias" in _codes(s)

    def test_capabilities_and_info_absorbed(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({"capabilities": {"tools": {"listChanged": True}}, "instructions": "be nice",
                             "server_info": {"name": "srv", "version": "9"}})
        s.initialize()
        assert s.server_capabilities == {"tools": {"listChanged": True}}
        assert s.server_info["name"] == "srv"
        assert s.state.instructions == "be nice"

    def test_initialize_error_raises(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({"mode": "modern", "versions": ["2026-07-28"]})
        with pytest.raises(McpError) as ei:
            s.initialize()
        assert "2026-07-28" in ei.value.message

    def test_roots_capability_advertised(self, session_factory, log_path: Path, tmp_path: Path) -> None:  # noqa: ANN001
        s = session_factory({})
        s.enable_roots(tmp_path)
        s.initialize()
        assert read_log(log_path)[0]["params"]["capabilities"] == {"roots": {"listChanged": True}}


class TestRequests:
    def test_list_and_call(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({})
        s.initialize()
        assert [t["name"] for t in s.list_tools()] == ["echo"]
        resp = s.call_tool("echo", {"text": "hi"})
        assert json.loads(resp["result"]["content"][0]["text"]) == {"text": "hi"}

    def test_call_raises_mcp_error(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({})
        s.initialize()
        with pytest.raises(McpError) as ei:
            s.call("no/such")
        assert ei.value.code == -32601

    def test_method_outside_version_noted_but_sent(self, session_factory, log_path: Path) -> None:  # noqa: ANN001
        s = session_factory({"versions": ["2025-06-18"]})
        s.initialize()
        with pytest.raises(McpError):
            s.call("tasks/list")
        assert any(m.get("method") == "tasks/list" for m in read_log(log_path))
        assert "sent-request-outside-version" in _codes(s)

    def test_complete_uses_uri_for_resources(self, session_factory, log_path: Path) -> None:  # noqa: ANN001
        s = session_factory({"completions": ["alpha", "beta"]})
        s.initialize()
        s.complete({"type": "ref/resource", "uri": "file:///{x}"}, "x", "a", {"y": "1"})
        req = [m for m in read_log(log_path) if m.get("method") == "completion/complete"][0]
        assert req["params"]["ref"] == {"type": "ref/resource", "uri": "file:///{x}"}
        assert req["params"]["context"] == {"arguments": {"y": "1"}}

    def test_server_request_answered(self, session_factory, log_path: Path, tmp_path: Path) -> None:  # noqa: ANN001
        s = session_factory({"tool_scripts": {"ask": [{"request": {"method": "roots/list"}}, {"reply": {}}]}})
        s.enable_roots(tmp_path)
        s.initialize()
        resp = s.call_tool("ask", {})
        answers = json.loads(resp["result"]["content"][0]["text"])
        assert answers[0]["result"]["roots"][0]["uri"] == tmp_path.resolve().as_uri()
        assert s.server_requests_seen == ["roots/list"]

    def test_unknown_server_request_gets_method_not_found(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({"tool_scripts": {"ask": [{"request": {"method": "sampling/createMessage"}}, {"reply": {}}]}})
        s.initialize()
        resp = s.call_tool("ask", {})
        answers = json.loads(resp["result"]["content"][0]["text"])
        assert answers[0]["error"]["code"] == -32601

    def test_notifications_reach_listeners(self, session_factory) -> None:  # noqa: ANN001
        note = {"jsonrpc": "2.0", "method": "notifications/elicitation/complete", "params": {"elicitationId": "e"}}
        s = session_factory({"versions": ["2025-06-18"], "tool_scripts": {"n": [{"notify": note}, {"reply": {"content": []}}]}})
        got = []
        s.notification_listeners.append(got.append)
        s.initialize()
        s.call_tool("n", {})
        assert got == [note]
        assert "notification-beyond-version" in _codes(s)

    def test_timeout_sends_cancel_and_drops_late_reply(self, session_factory, log_path: Path) -> None:  # noqa: ANN001
        s = session_factory({"tool_scripts": {"slow": [{"sleep": 1.0}, {"reply": {"content": []}}]}})
        s.initialize()
        with pytest.raises(ReceiveTimeout):
            s.call_tool("slow", {}, timeout=0.2)
        cancel = [m for m in _wait_log(log_path, "notifications/cancelled")]
        assert cancel and cancel[0]["params"]["reason"] == "client timeout"
        # The late reply arrives and is discarded without a finding.
        s.call("ping")
        assert "unmatched-response-id" not in _codes(s)

    def test_reserved_ids_skipped(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({})
        s.reserve_ids([1, 2])
        assert s._next_id() == 3


def _wait_log(path: Path, method: str, tries: int = 50) -> list[dict]:
    import time

    for _ in range(tries):
        found = [m for m in read_log(path) if isinstance(m, dict) and m.get("method") == method]
        if found:
            return found
        time.sleep(0.05)
    return []


class TestFraming:
    def test_modern_meta(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({})
        s.state.era = "modern"
        s.state.version = "2026-07-28"
        s.log_level = "debug"
        req = s.build_request("tools/list", None)
        meta = req["params"]["_meta"]
        assert meta[META_PROTOCOL_VERSION] == "2026-07-28"
        assert meta[META_CLIENT_INFO]["name"] == "mcp-test-driver"
        assert META_CLIENT_CAPABILITIES in meta
        assert meta[META_LOG_LEVEL] == "debug"

    def test_handshake_requests_have_no_protocol_meta(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({})
        assert "params" not in s.build_request("ping", None) or "_meta" not in s.build_request("ping", None)["params"]

    def test_progress_otel_and_overrides(self, session_factory) -> None:  # noqa: ANN001
        s = session_factory({})
        s.progress = True
        s.otel = True
        s.meta_overrides = {"com.example/x": 1}
        meta = s.build_request("tools/call", {"name": "t"})["params"]["_meta"]
        assert meta["progressToken"]
        assert meta["traceparent"].startswith("00-")
        assert meta["com.example/x"] == 1
        assert "progressToken" not in s.build_request("ping", None).get("params", {}).get("_meta", {})


class TestHttpWire:
    def _http_session(self, srv) -> McpSession:  # noqa: ANN001
        t = HttpTransport(srv.url, timeout=10)
        t.trace = False
        return McpSession(t)

    def test_version_header_after_negotiation(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({"versions": ["2025-06-18"], "tools": SAMPLE_TOOLS})
        s = self._http_session(srv)
        s.initialize()
        s.list_tools()
        assert "MCP-Protocol-Version" not in srv.headers_for("initialize")[0]
        assert srv.headers_for("tools/list")[0]["MCP-Protocol-Version"] == "2025-06-18"

    def test_no_version_header_for_2025_03_26(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({"versions": ["2025-03-26"], "tools": SAMPLE_TOOLS})
        s = self._http_session(srv)
        s.initialize()
        s.list_tools()
        assert "MCP-Protocol-Version" not in srv.headers_for("tools/list")[0]

    def test_error_status_with_response_body_is_the_response(self, fake_http) -> None:  # noqa: ANN001
        body = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad"}}
        srv = fake_http({"status_for": {"initialize": [400, body, {}]}})
        s = self._http_session(srv)
        with pytest.raises(McpError, match="bad"):
            s.initialize()
        assert "http-error-status-with-jsonrpc-body" in _codes(s)


def test_error_from() -> None:
    assert error_from({"result": {}}) is None
    e = error_from({"error": {"code": -1, "message": "\x1b[31mx"}})
    assert e is not None and e.message == "x"
    assert error_from({"error": {"code": "weird"}}).code == -1  # type: ignore[union-attr]
