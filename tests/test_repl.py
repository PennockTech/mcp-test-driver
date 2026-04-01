# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for the REPL module."""

from __future__ import annotations

import json

import pytest

from mcp_test_driver.repl import SessionCache, _print_result


SAMPLE_TOOLS: list[dict] = [
    {
        "name": "test_tool",
        "description": "A test tool",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "detail": {"type": "string", "enum": ["full", "summary"]},
            },
        },
    },
]


class TestSessionCache:
    """Tests for SessionCache."""

    def test_build_populates_tools(self) -> None:
        cache = SessionCache.build(SAMPLE_TOOLS, {"name": "test"})
        assert cache.tools == SAMPLE_TOOLS
        assert cache.server_info == {"name": "test"}

    def test_build_creates_completion_state(self) -> None:
        cache = SessionCache.build(SAMPLE_TOOLS, {})
        assert "test_tool" in cache.completion.tool_names

    def test_build_empty_tools(self) -> None:
        cache = SessionCache.build([], {})
        assert cache.tools == []
        assert cache.completion.tool_names == set()


class TestPrintResult:
    """Tests for _print_result output formatting."""

    def test_json_text_is_pretty_printed(self, capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
        resp = {
            "result": {
                "content": [{"text": json.dumps({"key": "value"}), "type": "text"}]
            }
        }
        _print_result(resp)
        captured = capsys.readouterr()
        assert '"key"' in captured.out
        assert '"value"' in captured.out

    def test_plain_text_output(self, capsys: object) -> None:
        resp = {"result": {"content": [{"text": "hello world", "type": "text"}]}}
        _print_result(resp)

    def test_error_result(self, capsys: object) -> None:
        resp = {
            "result": {
                "isError": True,
                "content": [{"text": "something went wrong", "type": "text"}],
            }
        }
        _print_result(resp)

    def test_empty_content(self) -> None:
        resp = {"result": {"content": []}}
        _print_result(resp)  # should not raise

    def test_missing_result(self) -> None:
        _print_result({})  # should not raise

    def test_jsonrpc_error_response(self, capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
        resp = {
            "error": {"code": -32600, "message": "Invalid Request"},
        }
        _print_result(resp)
        captured = capsys.readouterr()
        assert "Server error" in captured.out
        assert "-32600" in captured.out

    def test_jsonrpc_error_with_data(self, capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
        resp = {
            "error": {
                "code": -32603,
                "message": "Internal",
                "data": "extra details",
            },
        }
        _print_result(resp)
        captured = capsys.readouterr()
        assert "extra details" in captured.out

    def test_non_dict_result(self) -> None:
        _print_result({"result": "just a string"})  # should not raise

    def test_non_list_content(self) -> None:
        _print_result({"result": {"content": "not a list"}})  # should not raise

    def test_content_item_not_dict(self) -> None:
        _print_result({"result": {"content": ["string item"]}})  # should not raise
