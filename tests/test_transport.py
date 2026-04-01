# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for transport layer."""

from __future__ import annotations

import io
import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from mcp_test_driver.transport import HttpTransport, StdioTransport, _frame


class TestFrame:
    """Tests for the _frame helper."""

    def test_basic_frame(self) -> None:
        result = _frame({"jsonrpc": "2.0", "id": 1})
        assert result.endswith(b"\n")
        parsed = json.loads(result)
        assert parsed == {"jsonrpc": "2.0", "id": 1}

    def test_compact_encoding(self) -> None:
        result = _frame({"a": 1, "b": 2})
        # Should use compact separators (no spaces)
        assert b": " not in result
        assert b", " not in result

    def test_utf8_encoding(self) -> None:
        result = _frame({"char": "✓"})
        # json.dumps escapes non-ASCII to \uXXXX by default
        parsed = json.loads(result)
        assert parsed["char"] == "✓"


class TestStdioTransport:
    """Tests for StdioTransport."""

    def _make_transport(self, responses: list[dict]) -> StdioTransport:
        """Create a StdioTransport with a mocked subprocess."""
        response_lines = b"".join(
            json.dumps(r, separators=(",", ":")).encode() + b"\n" for r in responses
        )
        mock_stdout = io.BytesIO(response_lines)
        mock_stdin = io.BytesIO()

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.stdin = mock_stdin
        mock_proc.stdout = mock_stdout
        mock_proc.wait = MagicMock()

        with patch("subprocess.Popen", return_value=mock_proc):
            transport = StdioTransport(["fake-cmd"])

        transport.trace = False
        return transport

    def test_request_sends_and_receives(self) -> None:
        transport = self._make_transport([{"jsonrpc": "2.0", "id": 1, "result": {}}])
        resp = transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})
        assert resp is not None
        assert resp["id"] == 1

    def test_request_returns_none_on_eof(self) -> None:
        transport = self._make_transport([])
        resp = transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})
        assert resp is None

    def test_notify_does_not_read(self) -> None:
        transport = self._make_transport([])
        # notify should not try to read a response
        transport.notify({"jsonrpc": "2.0", "method": "notifications/test"})

    def test_close_cleans_up(self) -> None:
        transport = self._make_transport([])
        proc = transport._proc
        assert proc is not None
        transport.close()
        assert transport._proc is None
        proc.wait.assert_called_once()

    def test_close_idempotent(self) -> None:
        transport = self._make_transport([])
        transport.close()
        transport.close()  # should not raise

    def test_reconnect_relaunches(self) -> None:
        transport = self._make_transport([])
        transport.trace = False
        old_proc = transport._proc

        new_proc = MagicMock()
        new_proc.stdin = io.BytesIO()
        new_proc.stdout = io.BytesIO()
        new_proc.wait = MagicMock()

        with patch("mcp_test_driver.transport.subprocess.Popen", return_value=new_proc):
            transport.reconnect()

        assert transport._proc is not None
        assert transport._proc is not old_proc

    def test_trace_on_sends_to_stderr(self, capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
        transport = self._make_transport(
            [{"jsonrpc": "2.0", "id": 1, "result": {}}]
        )
        transport.trace = True
        transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})
        captured = capsys.readouterr()
        assert ">>>" in captured.err
        assert "<<<" in captured.err

    def test_trace_off_no_stderr(self, capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
        transport = self._make_transport(
            [{"jsonrpc": "2.0", "id": 1, "result": {}}]
        )
        transport.trace = False
        transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})
        captured = capsys.readouterr()
        assert ">>>" not in captured.err
        assert "<<<" not in captured.err


class TestHttpTransport:
    """Tests for HttpTransport."""

    def _make_transport(self) -> HttpTransport:
        transport = HttpTransport("https://example.com/mcp")
        transport.trace = False
        return transport

    def test_headers_without_session(self) -> None:
        transport = self._make_transport()
        headers = transport._headers()
        assert headers["Content-Type"] == "application/json"
        assert "Accept" in headers
        assert "Mcp-Session-Id" not in headers

    def test_headers_with_session(self) -> None:
        transport = self._make_transport()
        transport._session_id = "test-session-123"
        headers = transport._headers()
        assert headers["Mcp-Session-Id"] == "test-session-123"

    def test_request_json_response(self) -> None:
        transport = self._make_transport()
        response_body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        ).encode()

        mock_resp = MagicMock()
        mock_resp.headers = {
            "Content-Type": "application/json",
        }
        mock_resp.status = 200
        mock_resp.read.return_value = response_body
        mock_resp.release_conn = MagicMock()

        transport._pool = MagicMock()
        transport._pool.request.return_value = mock_resp

        result = transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})
        assert result is not None
        assert result["result"]["ok"] is True

    def test_request_captures_session_id(self) -> None:
        transport = self._make_transport()
        response_body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode()

        mock_resp = MagicMock()
        mock_resp.headers = {
            "Content-Type": "application/json",
            "Mcp-Session-Id": "new-session-456",
        }
        mock_resp.status = 200
        mock_resp.read.return_value = response_body
        mock_resp.release_conn = MagicMock()

        transport._pool = MagicMock()
        transport._pool.request.return_value = mock_resp

        transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})
        assert transport._session_id == "new-session-456"

    def test_notify_202_accepted(self) -> None:
        transport = self._make_transport()

        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": ""}
        mock_resp.status = 202
        mock_resp.read.return_value = b""
        mock_resp.release_conn = MagicMock()

        transport._pool = MagicMock()
        transport._pool.request.return_value = mock_resp

        transport.notify({"jsonrpc": "2.0", "method": "notifications/test"})

    def test_reconnect_clears_session(self) -> None:
        transport = self._make_transport()
        transport._session_id = "old-session"
        transport.reconnect()
        assert transport._session_id is None

    def test_parse_sse_single_event(self) -> None:
        transport = self._make_transport()
        sse_data = [
            b'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\r\n',
            b"\r\n",
        ]
        result = transport._parse_sse(iter(sse_data))
        assert result is not None
        assert result["result"]["ok"] is True

    def test_parse_sse_multiple_events(self) -> None:
        transport = self._make_transport()
        sse_data = [
            b'data: {"jsonrpc":"2.0","method":"progress"}\r\n',
            b"\r\n",
            b'data: {"jsonrpc":"2.0","id":1,"result":{"done":true}}\r\n',
            b"\r\n",
        ]
        result = transport._parse_sse(iter(sse_data))
        assert result is not None
        # Returns the last message
        assert result["result"]["done"] is True

    def test_parse_sse_multiline_data(self) -> None:
        transport = self._make_transport()
        sse_data = [
            b"data: {\r\n",
            b'data: "jsonrpc":"2.0","id":1,"result":{}\r\n',
            b"data: }\r\n",
            b"\r\n",
        ]
        result = transport._parse_sse(iter(sse_data))
        assert result is not None

    def test_parse_sse_ignores_comments(self) -> None:
        transport = self._make_transport()
        sse_data = [
            b": this is a comment\r\n",
            b'data: {"jsonrpc":"2.0","id":1,"result":{}}\r\n',
            b"\r\n",
        ]
        result = transport._parse_sse(iter(sse_data))
        assert result is not None

    def test_parse_sse_empty_stream(self) -> None:
        transport = self._make_transport()
        result = transport._parse_sse(iter([]))
        assert result is None

    def test_unexpected_content_type_raises(self) -> None:
        transport = self._make_transport()

        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.status = 200
        mock_resp.read.return_value = b"<html>error</html>"
        mock_resp.release_conn = MagicMock()

        transport._pool = MagicMock()
        transport._pool.request.return_value = mock_resp

        with pytest.raises(ValueError, match="Unexpected Content-Type"):
            transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})
