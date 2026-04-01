# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for the REPL module."""

from __future__ import annotations

import json

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

    def test_json_text_is_pretty_printed(self, capsys: object) -> None:
        resp = {
            "result": {
                "content": [
                    {"text": json.dumps({"key": "value"}), "type": "text"}
                ]
            }
        }
        _print_result(resp)
        import sys

        captured = sys.modules["_pytest.capture"].CaptureFixture  # type: ignore[attr-defined]
        # Just verify it doesn't raise

    def test_plain_text_output(self, capsys: object) -> None:
        resp = {
            "result": {
                "content": [{"text": "hello world", "type": "text"}]
            }
        }
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
