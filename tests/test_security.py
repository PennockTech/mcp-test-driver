# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for defences against malicious servers (see docs/security-model.md)."""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from mcp_test_driver.completion import CompletionState
from mcp_test_driver.protocol import McpSession
from mcp_test_driver.sanitize import safe_json, sanitize
from mcp_test_driver.transport import (
    MAX_LINE_BYTES,
    MAX_RESPONSE_BYTES,
    HttpTransport,
    SessionExpiredError,
    StdioTransport,
    TransportError,
    check_header,
    redact_headers,
)
from mcp_test_driver.transport.sse import SseLimitExceeded, SseParser


class MockResp:
    """Just enough of urllib3's response for HttpTransport."""

    def __init__(self, status: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.status = status
        self.headers = headers
        self._body = body
        self.released = False

    def read(self, amt: int | None = None) -> bytes:
        data, self._body = (self._body, b"") if amt is None else (self._body[:amt], self._body[amt:])
        return data

    def read1(self, amt: int = -1) -> bytes:
        return self.read(amt if amt > 0 else None)

    def release_conn(self) -> None:
        self.released = True


class MockPool:
    def __init__(self, *responses: MockResp) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def request(self, method: str, url: str, **kw: Any) -> MockResp:
        self.calls.append((method, kw.get("headers", {})))
        return self.responses.pop(0) if self.responses else MockResp(200, {})

    def clear(self) -> None:
        pass


def _http(*responses: MockResp) -> tuple[HttpTransport, MockPool]:
    t = HttpTransport("https://example.com/mcp")
    t.trace = False
    pool = MockPool(*responses)
    t._pool = pool  # type: ignore[assignment]
    return t, pool


REQ = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


class TestSanitize:
    def test_plain_text_unchanged(self) -> None:
        assert sanitize("hello world") == "hello world"

    def test_strips_ansi_color(self) -> None:
        assert sanitize("\x1b[31mred\x1b[0m") == "red"

    def test_strips_title_change(self) -> None:
        assert sanitize("\x1b]0;evil\x07ok") == "ok"

    def test_strips_screen_clear(self) -> None:
        assert sanitize("\x1b[2Jcleared") == "cleared"

    def test_strips_nul_and_controls(self) -> None:
        assert sanitize("a\x00b\x07c\x7fd\x9be") == "abcde"

    def test_preserves_newlines_and_tabs(self) -> None:
        assert sanitize("a\nb\tc\r") == "a\nb\tc\r"

    def test_strips_osc_clipboard_injection(self) -> None:
        assert sanitize("\x1b]52;c;ZXZpbA==\x07x") == "x"

    def test_strips_bidi_overrides(self) -> None:
        assert sanitize("admin\u202e\u2066txt.exe") == "admintxt.exe"

    def test_unicode_preserved(self) -> None:
        assert sanitize("✓ café 日本") == "✓ café 日本"

    def test_safe_json_strips_c1_and_bidi(self) -> None:
        out = safe_json({"k": "a\x9bb\u202ec"})
        assert "\x9b" not in out and "\u202e" not in out


class TestCompletionSanitization:
    def _state(self, name: str, desc: str = "", props: dict | None = None) -> CompletionState:
        return CompletionState.from_tools(
            [{"name": name, "description": desc, "inputSchema": {"type": "object", "properties": props or {}}}]
        )

    def test_ansi_in_tool_name_stripped(self) -> None:
        assert self._state("\x1b[31mevil").tool_names == {"evil"}

    def test_ansi_in_description_stripped(self) -> None:
        assert self._state("t", "\x1b]0;x\x07desc").tool_descriptions["t"] == "desc"

    def test_spaces_replaced_and_real_name_kept(self) -> None:
        state = self._state("my tool")
        assert state.tool_names == {"my_tool"}
        assert state.real_tool_name("my_tool") == "my tool"

    def test_empty_and_prefix_only_names_skipped(self) -> None:
        assert self._state("").tool_names == set()
        assert self._state("///").tool_names == set()

    def test_builtin_prefix_stripped(self) -> None:
        assert self._state("/quit").tool_names == {"quit"}
        assert self._state("//list").tool_names == {"list"}

    def test_enum_values_sanitized(self) -> None:
        state = self._state("t", props={"m": {"type": "string", "enum": ["\x1b[31mfull", "summary"]}})
        assert state.arg_enums["t:m"] == ["full", "summary"]

    def test_non_string_name_skipped(self) -> None:
        state = CompletionState.from_tools([{"name": 42, "inputSchema": {}}])
        assert state.tool_names == set()


class TestStdioLimits:
    def test_oversized_line_discarded_then_recovers(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = (
            "import sys; sys.stdout.write('x' * %d + '\\n'); "
            "sys.stdout.write('{\"jsonrpc\":\"2.0\",\"id\":5,\"result\":{}}\\n'); sys.stdout.flush()"
            % (MAX_LINE_BYTES + 100)
        )
        t = StdioTransport([sys.executable, "-c", code], timeout=20)
        t.trace = False
        assert t.receive()["id"] == 5
        assert "discarding" in capsys.readouterr().err
        t.close()


class TestHttpRedirectRejection:
    @pytest.mark.parametrize("status", [301, 302, 307, 308])
    def test_redirect_raises(self, status: int) -> None:
        t, _ = _http(MockResp(status, {"Location": "http://169.254.169.254/metadata"}))
        with pytest.raises(TransportError, match="redirect"):
            t.send(REQ)

    def test_location_sanitized_in_error(self) -> None:
        t, _ = _http(MockResp(302, {"Location": "http://x/\x1b[31m"}))
        with pytest.raises(TransportError) as ei:
            t.send(REQ)
        assert "\x1b" not in str(ei.value)


class TestHttpStatusValidation:
    def test_500_raises(self) -> None:
        t, _ = _http(MockResp(500, {"Content-Type": "text/plain"}, b"Internal Server Error"))
        with pytest.raises(TransportError, match="HTTP 500"):
            t.send(REQ)

    def test_401_raises_auth_required(self) -> None:
        from mcp_test_driver.transport import AuthRequiredError

        t, _ = _http(MockResp(401, {"WWW-Authenticate": 'Bearer resource_metadata="https://x/prm"'}, b""))
        with pytest.raises(AuthRequiredError) as ei:
            t.send(REQ)
        assert "resource_metadata" in ei.value.www_authenticate


class TestHttpBodySizeLimit:
    def test_oversized_json_response_raises(self) -> None:
        t, _ = _http(MockResp(200, {"Content-Type": "application/json"}, b"x" * (MAX_RESPONSE_BYTES + 100)))
        with pytest.raises(TransportError, match="exceeds"):
            t.send(REQ)

    def test_oversized_sse_stream_raises(self) -> None:
        parser = SseParser(1024)
        with pytest.raises(SseLimitExceeded):
            for _ in range(100):
                parser.feed(b"data: " + b"x" * 100 + b"\n")

    def test_oversized_sse_stream_via_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("mcp_test_driver.transport.http.MAX_RESPONSE_BYTES", 4096)
        body = (b"data: " + b"x" * 1024 + b"\n") * 20
        t, _ = _http(MockResp(200, {"Content-Type": "text/event-stream"}, body))
        t.send(REQ)
        with pytest.raises(TransportError, match="exceeds"):
            t.receive(timeout=5)


class TestSessionIdSanitization:
    def test_session_id_sanitized(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode()
        t, _ = _http(MockResp(200, {"Content-Type": "application/json", "Mcp-Session-Id": "valid-id\r\nInjected: bad"}, body))
        t.send(REQ)
        assert t.session_id == "valid-idInjected:bad"


class TestSessionExpired:
    def test_404_with_session_raises_expired(self) -> None:
        t, _ = _http(MockResp(404, {}, b""))
        t._session_id = "old"
        with pytest.raises(SessionExpiredError):
            t.send(REQ)
        assert t.session_id is None

    def test_404_without_session_is_generic_error(self) -> None:
        t, _ = _http(MockResp(404, {}, b""))
        with pytest.raises(TransportError, match="HTTP 404") as ei:
            t.send(REQ)
        assert not isinstance(ei.value, SessionExpiredError)


class TestHttpDeleteOnClose:
    def test_close_sends_delete_when_session_active(self) -> None:
        t, pool = _http(MockResp(200, {}))
        t._session_id = "s1"
        t.close()
        assert pool.calls and pool.calls[0][0] == "DELETE"
        assert pool.calls[0][1]["Mcp-Session-Id"] == "s1"

    def test_close_no_delete_without_session(self) -> None:
        t, pool = _http()
        t.close()
        assert pool.calls == []

    def test_close_delete_failure_ignored(self) -> None:
        t, pool = _http()
        t._session_id = "s1"

        def boom(*a: Any, **k: Any) -> MockResp:
            raise OSError("down")

        pool.request = boom  # type: ignore[method-assign]
        t.close()


class TestHeaderSafety:
    @pytest.mark.parametrize("name,value", [("X", "a\r\nB: c"), ("X\nY", "v"), ("", "v"), ("A:B", "v")])
    def test_bad_headers_rejected(self, name: str, value: str) -> None:
        with pytest.raises(ValueError):
            check_header(name, value)

    def test_redaction(self) -> None:
        out = redact_headers({"Authorization": "Bearer abc", "X": "1"})
        assert out == {"Authorization": "Bearer <redacted>", "X": "1"}


class TestResponseIdMatching:
    def test_stray_response_reported_and_skipped(self, fake_stdio, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
        # A server that answers with a wrong id first, then the right one.
        code = (
            "import sys, json\n"
            "for line in sys.stdin:\n"
            "    m = json.loads(line)\n"
            "    if 'id' in m and 'method' in m:\n"
            "        print(json.dumps({'jsonrpc':'2.0','id':999,'result':{}}))\n"
            "        print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'ok':True}}))\n"
            "        sys.stdout.flush()\n"
        )
        t = StdioTransport([sys.executable, "-c", code], timeout=5)
        t.trace = False
        s = McpSession(t)
        assert s.call("ping") == {"ok": True}
        assert "matches no outstanding request" in capsys.readouterr().err
        assert any(f.code == "unmatched-response-id" for f in s.findings)
        t.close()

    def test_late_response_to_cancelled_request_discarded(self, capsys: pytest.CaptureFixture[str]) -> None:
        t = StdioTransport([sys.executable, "-c", "pass"], timeout=5)
        s = McpSession(t)
        s._abandoned.add(3)
        s.handle_incoming({"jsonrpc": "2.0", "id": 3, "result": {}})
        assert "late response" in capsys.readouterr().err
        assert not s.findings.items
        t.close()


class TestPagination:
    def test_runaway_pagination_capped(self, fake_stdio) -> None:  # noqa: ANN001
        # page_size 1 over 150 tools would need 150 pages; we stop at 100.
        tools = [{"name": f"t{i}", "inputSchema": {"type": "object"}} for i in range(150)]
        t = StdioTransport(fake_stdio({"tools": tools, "page_size": 1}), timeout=10)
        t.trace = False
        s = McpSession(t)
        s.initialize()
        assert len(s.list_tools()) == 100
        assert any(f.code == "pagination-runaway" for f in s.findings)
        t.close()

    def test_multi_page(self, fake_stdio) -> None:  # noqa: ANN001
        tools = [{"name": f"t{i}", "inputSchema": {"type": "object"}} for i in range(5)]
        t = StdioTransport(fake_stdio({"tools": tools, "page_size": 2}), timeout=10)
        t.trace = False
        s = McpSession(t)
        s.initialize()
        assert [x["name"] for x in s.list_tools()] == [f"t{i}" for i in range(5)]
        t.close()
