# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for the stdio and Streamable HTTP transports and the SSE parser."""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from mcp_test_driver.transport import (
    ConnectionClosed,
    HttpStatusError,
    HttpTransport,
    NoPendingMessages,
    ReceiveTimeout,
    StdioTransport,
    TransportError,
    encode_header_value,
)
from mcp_test_driver.transport.sse import SseParser, iter_events
from mcp_test_driver.transport.stdio import _frame


def _req(i: int, method: str = "ping", params: dict | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {"jsonrpc": "2.0", "id": i, "method": method}
    if params is not None:
        m["params"] = params
    return m


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


class TestFrame:
    def test_compact_newline_terminated(self) -> None:
        assert _frame({"a": 1, "b": [1, 2]}) == b'{"a":1,"b":[1,2]}\n'

    def test_utf8(self) -> None:
        out = _frame({"c": "✓"})
        assert out.endswith(b"\n")
        assert json.loads(out)["c"] == "✓"


class TestStdioTransport:
    def _start(self, fake_stdio, scenario: dict) -> StdioTransport:  # noqa: ANN001
        t = StdioTransport(fake_stdio(scenario), timeout=10)
        t.trace = False
        return t

    def test_round_trip(self, fake_stdio) -> None:  # noqa: ANN001
        t = self._start(fake_stdio, {"mode": "legacy"})
        t.send(_req(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "x", "version": "1"}}))
        msg = t.receive()
        assert msg["id"] == 1 and msg["result"]["protocolVersion"] == "2025-11-25"
        t.close()

    def test_eof_raises_connection_closed(self) -> None:
        t = StdioTransport(_python("pass"), timeout=5)
        t.trace = False
        with pytest.raises(ConnectionClosed, match="exit status 0"):
            t.receive()
        t.close()

    def test_timeout_even_on_partial_line(self) -> None:
        # A server that writes half a message and stalls must not hang us.
        code = "import sys,time; sys.stdout.write('{\"jsonrpc\"'); sys.stdout.flush(); time.sleep(30)"
        t = StdioTransport(_python(code), timeout=0.5)
        t.trace = False
        with pytest.raises(ReceiveTimeout):
            t.receive()
        t.close()

    def test_final_message_without_newline_delivered(self) -> None:
        code = "import sys; sys.stdout.write('{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}')"
        t = StdioTransport(_python(code), timeout=5)
        t.trace = False
        assert t.receive()["id"] == 1
        t.close()

    def test_malformed_json_skipped(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = "print('not json'); print('{\"jsonrpc\":\"2.0\",\"id\":7,\"result\":{}}')"
        t = StdioTransport(_python(code), timeout=5)
        t.trace = False
        assert t.receive()["id"] == 7
        assert "Malformed JSON" in capsys.readouterr().err
        t.close()

    def test_batch_expanded(self) -> None:
        code = "print('[{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}},{\"jsonrpc\":\"2.0\",\"method\":\"n\"}]')"
        t = StdioTransport(_python(code), timeout=5)
        t.trace = False
        assert t.receive()["id"] == 1
        assert t.receive()["method"] == "n"
        assert t.batches_received == 1
        t.close()

    def test_poll_does_not_block(self) -> None:
        t = StdioTransport(_python("import time; time.sleep(30)"), timeout=5)
        t.trace = False
        assert t.poll() == []
        t.close()

    def test_stderr_is_sanitized_and_printed_by_caller(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = "import sys; sys.stderr.write('\\x1b[31mhello\\n'); sys.stderr.flush()"
        t = StdioTransport(_python(code), timeout=5)
        t.trace = False
        with pytest.raises(ConnectionClosed):
            t.receive()
        err = capsys.readouterr().err
        assert "[server stderr] hello" in err
        assert "\x1b[31m" not in err
        t.close()

    def test_command_not_found(self) -> None:
        with pytest.raises(TransportError, match="Command not found"):
            StdioTransport(["/nonexistent/mcp-server-xyz"])

    def test_send_after_exit(self) -> None:
        t = StdioTransport(_python("pass"), timeout=5)
        t.trace = False
        t._proc.wait()  # type: ignore[union-attr]
        with pytest.raises((ConnectionClosed, TransportError)):
            for _ in range(50):  # the pipe may buffer the first writes
                t.send(_req(1))
        t.close()

    def test_close_idempotent_and_reconnect(self, fake_stdio) -> None:  # noqa: ANN001
        t = self._start(fake_stdio, {})
        first = t.pid
        t.reconnect()
        assert t.pid is not None and t.pid != first
        t.close()
        t.close()

    def test_trace(self, fake_stdio, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
        t = self._start(fake_stdio, {"mode": "legacy", "on_unknown_pre_init": "error"})
        t.trace = True
        t.send(_req(1, "tools/list"))
        t.receive()
        err = capsys.readouterr().err
        assert ">>>" in err and "<<<" in err
        t.close()


class TestSseParser:
    def _events(self, raw: bytes) -> tuple[list, SseParser]:
        parser = SseParser(1 << 20)
        return list(iter_events(raw.splitlines(keepends=True), parser)), parser

    def test_single_event(self) -> None:
        evs, _ = self._events(b"event: message\ndata: {\"a\":1}\n\n")
        assert [(e.event, e.data) for e in evs] == [("message", '{"a":1}')]

    def test_default_event_type_and_no_space(self) -> None:
        evs, _ = self._events(b"data:{\"a\":1}\n\n")
        assert evs[0].event == "message" and evs[0].data == '{"a":1}'

    def test_multiline_data(self) -> None:
        evs, _ = self._events(b"data: {\"a\":\ndata: 1}\n\n")
        assert json.loads(evs[0].data) == {"a": 1}

    def test_comments_ignored(self) -> None:
        evs, _ = self._events(b": keepalive\n\ndata: x\n\n")
        assert [e.data for e in evs] == ["x"]

    def test_priming_event_sets_id_without_dispatch(self) -> None:
        evs, p = self._events(b"id: abc\ndata:\n\nretry: 1500\n\n")
        assert evs == []
        assert p.primed and p.last_event_id == "abc" and p.retry_ms == 1500

    def test_priming_does_not_leak_into_next_event(self) -> None:
        evs, _ = self._events(b"id: p\ndata:\n\ndata: real\n\n")
        assert [e.data for e in evs] == ["real"]

    def test_ids_persist(self) -> None:
        evs, p = self._events(b"id: 1\ndata: a\n\ndata: b\n\n")
        assert evs[0].id == "1" and evs[1].id is None and p.last_event_id == "1"

    def test_trailing_event_without_blank_line(self) -> None:
        evs, _ = self._events(b"data: tail")
        assert [e.data for e in evs] == ["tail"]

    def test_crlf(self) -> None:
        evs, _ = self._events(b"data: x\r\n\r\n")
        assert evs[0].data == "x"


class TestHttpTransport:
    def _t(self, srv) -> HttpTransport:  # noqa: ANN001
        t = HttpTransport(srv.url, timeout=10)
        t.trace = False
        return t

    def _init(self, version: str = "2025-11-25") -> dict:
        return _req(1, "initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})

    def test_json_response(self, fake_http) -> None:  # noqa: ANN001
        t = self._t(fake_http({}))
        t.send(self._init())
        assert t.receive()["result"]["serverInfo"]["name"] == "fake-server"

    def test_sse_response_carries_all_messages(self, fake_http) -> None:  # noqa: ANN001
        notify = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progressToken": 1, "progress": 1}}
        srv = fake_http({
            "http_mode": "sse",
            "tools": [{"name": "t", "inputSchema": {"type": "object"}}],
            "tool_scripts": {"t": [{"notify": notify}, {"reply": {"content": []}}]},
        })
        t = self._t(srv)
        t.send(self._init())
        t.receive()
        t.send(_req(2, "tools/call", {"name": "t"}))
        assert t.receive()["method"] == "notifications/progress"
        assert t.receive()["id"] == 2
        with pytest.raises(NoPendingMessages):
            t.receive()

    def test_server_request_answered_mid_stream(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({
            "http_mode": "sse",
            "tool_scripts": {"t": [{"request": {"method": "roots/list"}}, {"reply": {}}]},
        })
        t = self._t(srv)
        t.send(self._init())
        t.receive()
        t.send(_req(2, "tools/call", {"name": "t"}))
        req = t.receive()
        assert req["method"] == "roots/list"
        t.send({"jsonrpc": "2.0", "id": req["id"], "result": {"roots": []}})
        resp = t.receive()
        assert resp["id"] == 2
        assert json.loads(resp["result"]["content"][0]["text"])[0]["result"] == {"roots": []}

    def test_session_id_round_trip_and_delete(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({"session_id": "abc123"})
        t = self._t(srv)
        t.send(self._init())
        t.receive()
        assert t.session_id == "abc123"
        t.send(_req(2, "tools/list"))
        t.receive()
        assert srv.headers_for("tools/list")[0]["Mcp-Session-Id"] == "abc123"
        t.close()
        assert any(m == "DELETE" for (m, *_) in srv.requests)

    def test_notification_202(self, fake_http) -> None:  # noqa: ANN001
        t = self._t(fake_http({}))
        t.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert t.last_status == 202
        assert t.poll() == []

    def test_wire_headers(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({"tools": [{"name": "café tool", "inputSchema": {"type": "object"}}]})
        t = self._t(srv)
        t.send(self._init())
        t.receive()
        t.wire.protocol_version = "2026-07-28"
        t.wire.version_header = True
        t.wire.method_headers = True
        t.send(_req(2, "tools/call", {"name": "café tool"}))
        t.receive()
        h = srv.headers_for("tools/call")[0]
        assert h["MCP-Protocol-Version"] == "2026-07-28"
        assert h["Mcp-Method"] == "tools/call"
        assert h["Mcp-Name"] == encode_header_value("café tool")
        assert h["Mcp-Name"].startswith("=?base64?")

    def test_error_status_keeps_jsonrpc_body(self, fake_http) -> None:  # noqa: ANN001
        body = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32022, "message": "Unsupported"}}
        t = self._t(fake_http({"status_for": {"server/discover": [400, body, {}]}}))
        with pytest.raises(HttpStatusError) as ei:
            t.send(_req(1, "server/discover"))
        assert ei.value.status == 400
        assert ei.value.messages[0]["error"]["code"] == -32022

    def test_extra_and_authorization_headers(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({})
        t = HttpTransport(srv.url, extra_headers={"X-Test": "1"})
        t.trace = False
        t.authorization = "Bearer s3cret"
        t.send(self._init())
        t.receive()
        h = srv.headers_for("initialize")[0]
        assert h["X-Test"] == "1" and h["Authorization"] == "Bearer s3cret"

    def test_header_injection_rejected(self) -> None:
        with pytest.raises(ValueError):
            HttpTransport("http://127.0.0.1:1/mcp", extra_headers={"X": "a\r\nInjected: 1"})

    def test_connection_failure(self) -> None:
        t = HttpTransport("http://127.0.0.1:1/mcp", timeout=2)
        t.trace = False
        with pytest.raises(TransportError, match="Connection failed"):
            t.send(_req(1))


class TestHeaderEncoding:
    def test_plain_ascii_unchanged(self) -> None:
        assert encode_header_value("get_weather") == "get_weather"
        assert encode_header_value("file:///a/b.json") == "file:///a/b.json"

    def test_non_ascii_and_sentinel_lookalikes_encoded(self) -> None:
        assert encode_header_value("naïve").startswith("=?base64?")
        assert encode_header_value("=?base64?abc?=").startswith("=?base64?")
        assert encode_header_value(" padded").startswith("=?base64?")
