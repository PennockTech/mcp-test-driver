# Copyright 2026 Phil Pennock — see LICENSE file.

"""Security-focused tests: sanitization, size limits, redirect rejection, ID matching."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from mcp_test_driver.completion import CompletionState
from mcp_test_driver.protocol import _check_id
from mcp_test_driver.transport import (
    MAX_LINE_BYTES,
    MAX_RESPONSE_BYTES,
    HttpTransport,
    StdioTransport,
    TransportError,
    sanitize,
)


class TestSanitize:
    """Tests for the sanitize() function."""

    def test_plain_text_unchanged(self) -> None:
        assert sanitize("hello world") == "hello world"

    def test_strips_ansi_color(self) -> None:
        assert sanitize("\033[31mred\033[0m") == "red"

    def test_strips_title_change(self) -> None:
        assert sanitize("\033]0;pwned\007normal") == "normal"

    def test_strips_screen_clear(self) -> None:
        assert sanitize("\033[2Jcleared") == "cleared"

    def test_strips_null_bytes(self) -> None:
        assert sanitize("he\x00llo") == "hello"

    def test_strips_control_chars(self) -> None:
        assert sanitize("abc\x01\x02\x03def") == "abcdef"

    def test_preserves_newlines_and_tabs(self) -> None:
        # Newlines and tabs are common in descriptions
        assert sanitize("line1\nline2\ttab") == "line1\nline2\ttab"

    def test_strips_osc_clipboard_injection(self) -> None:
        # OSC 52 clipboard manipulation attempt
        assert sanitize("\033]52;c;aGVsbG8=\007") == ""

    def test_unicode_preserved(self) -> None:
        assert sanitize("✓ snowman ☃") == "✓ snowman ☃"


class TestCompletionSanitization:
    """Tests that tool names/descriptions are sanitized in CompletionState."""

    def test_ansi_in_tool_name_stripped(self) -> None:
        tools = [
            {
                "name": "\033[31mevil_tool\033[0m",
                "description": "test",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
        state = CompletionState.from_tools(tools)
        assert "evil_tool" in state.tool_names
        # No ANSI codes in the name
        for name in state.tool_names:
            assert "\033" not in name

    def test_ansi_in_description_stripped(self) -> None:
        tools = [
            {
                "name": "test_tool",
                "description": "\033]0;pwned\007real description",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
        state = CompletionState.from_tools(tools)
        desc = state.tool_descriptions["test_tool"]
        assert "\033" not in desc
        assert "real description" in desc

    def test_spaces_in_tool_name_replaced(self) -> None:
        tools = [
            {
                "name": "tool with spaces",
                "description": "test",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
        state = CompletionState.from_tools(tools)
        for name in state.tool_names:
            assert " " not in name

    def test_empty_tool_name_skipped(self) -> None:
        tools = [
            {
                "name": "",
                "description": "empty name",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
        state = CompletionState.from_tools(tools)
        assert len(state.tool_names) == 0


class TestStdioSizeLimits:
    """Tests for stdio readline size limits."""

    def test_oversized_line_returns_none(self) -> None:
        # Create a line that exceeds MAX_LINE_BYTES without a newline
        big_data = b"x" * (MAX_LINE_BYTES + 100) + b"\n"
        mock_stdout = io.BytesIO(big_data)
        mock_stdin = io.BytesIO()

        mock_proc = MagicMock()
        mock_proc.stdin = mock_stdin
        mock_proc.stdout = mock_stdout
        mock_proc.wait = MagicMock()

        with patch(
            "mcp_test_driver.transport.subprocess.Popen", return_value=mock_proc
        ):
            transport = StdioTransport(["fake"])

        transport.trace = False
        result = transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})
        assert result is None


class TestHttpRedirectRejection:
    """Tests that HTTP redirects are rejected."""

    def test_301_redirect_raises(self) -> None:
        transport = HttpTransport("https://example.com/mcp")
        transport.trace = False

        mock_resp = MagicMock()
        mock_resp.status = 301
        mock_resp.headers = {
            "Content-Type": "",
            "Location": "http://169.254.169.254/metadata",
        }
        mock_resp.read.return_value = b""
        mock_resp.release_conn = MagicMock()

        transport._pool = MagicMock()
        transport._pool.request.return_value = mock_resp

        with pytest.raises(TransportError, match="redirect"):
            transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})

    def test_302_redirect_raises(self) -> None:
        transport = HttpTransport("https://example.com/mcp")
        transport.trace = False

        mock_resp = MagicMock()
        mock_resp.status = 302
        mock_resp.headers = {
            "Content-Type": "",
            "Location": "http://localhost:9090/internal",
        }
        mock_resp.read.return_value = b""
        mock_resp.release_conn = MagicMock()

        transport._pool = MagicMock()
        transport._pool.request.return_value = mock_resp

        with pytest.raises(TransportError, match="redirect"):
            transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})


class TestHttpStatusValidation:
    """Tests that non-2xx HTTP status codes are rejected."""

    def test_500_raises(self) -> None:
        transport = HttpTransport("https://example.com/mcp")
        transport.trace = False

        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_resp.read.return_value = b"Internal Server Error"
        mock_resp.release_conn = MagicMock()

        transport._pool = MagicMock()
        transport._pool.request.return_value = mock_resp

        with pytest.raises(TransportError, match="HTTP 500"):
            transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})

    def test_401_raises(self) -> None:
        transport = HttpTransport("https://example.com/mcp")
        transport.trace = False

        mock_resp = MagicMock()
        mock_resp.status = 401
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_resp.read.return_value = b"Unauthorized"
        mock_resp.release_conn = MagicMock()

        transport._pool = MagicMock()
        transport._pool.request.return_value = mock_resp

        with pytest.raises(TransportError, match="HTTP 401"):
            transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})


class TestHttpBodySizeLimit:
    """Tests that oversized HTTP response bodies are rejected."""

    def test_oversized_json_response_raises(self) -> None:
        transport = HttpTransport("https://example.com/mcp")
        transport.trace = False

        big_body = b"x" * (MAX_RESPONSE_BYTES + 100)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.read.return_value = big_body
        mock_resp.release_conn = MagicMock()

        transport._pool = MagicMock()
        transport._pool.request.return_value = mock_resp

        with pytest.raises(TransportError, match="exceeds"):
            transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})

    def test_oversized_sse_stream_raises(self) -> None:
        transport = HttpTransport("https://example.com/mcp")
        transport.trace = False

        # Generate SSE data exceeding the limit
        big_lines = [b"data: " + b"x" * 1024 + b"\r\n"] * (
            MAX_RESPONSE_BYTES // 1024 + 100
        )

        with pytest.raises(TransportError, match="SSE stream exceeds"):
            transport._parse_sse(iter(big_lines))


class TestSessionIdSanitization:
    """Tests that the Mcp-Session-Id header is sanitized."""

    def test_session_id_sanitized(self) -> None:
        transport = HttpTransport("https://example.com/mcp")
        transport.trace = False

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {
            "Content-Type": "application/json",
            "Mcp-Session-Id": "valid-id\r\nInjected-Header: bad",
        }
        mock_resp.read.return_value = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {}}
        ).encode()
        mock_resp.release_conn = MagicMock()

        transport._pool = MagicMock()
        transport._pool.request.return_value = mock_resp

        transport.request({"jsonrpc": "2.0", "id": 1, "method": "test"})
        # Session ID should not contain CRLF
        assert transport._session_id is not None
        assert "\r" not in transport._session_id
        assert "\n" not in transport._session_id


class TestResponseIdMatching:
    """Tests for JSON-RPC response ID validation."""

    def test_matching_id_no_warning(self, capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
        _check_id({"jsonrpc": "2.0", "id": 1, "result": {}}, 1)
        captured = capsys.readouterr()
        assert "Warning" not in captured.err

    def test_mismatched_id_warns(self, capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
        _check_id({"jsonrpc": "2.0", "id": 99, "result": {}}, 1)
        captured = capsys.readouterr()
        assert "Warning" in captured.err
        assert "id=99" in captured.err
        assert "id=1" in captured.err

    def test_missing_id_warns(self, capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
        _check_id({"jsonrpc": "2.0", "result": {}}, 1)
        captured = capsys.readouterr()
        assert "Warning" in captured.err
