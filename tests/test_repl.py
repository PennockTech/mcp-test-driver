# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for the REPL module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

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


def _mock_session(
    tools: list[dict] | None = None,
    server_info: dict | None = None,
    capabilities: dict | None = None,
) -> MagicMock:
    """Create a mock McpSession for SessionCache.build."""
    session = MagicMock()
    session.list_tools.return_value = tools or []
    session.server_info = server_info or {}
    session.server_capabilities = capabilities or {}
    session.list_resources.return_value = []
    session.list_resource_templates.return_value = []
    session.list_prompts.return_value = []
    return session


class TestSessionCache:
    """Tests for SessionCache."""

    def test_build_populates_tools(self) -> None:
        session = _mock_session(tools=SAMPLE_TOOLS, server_info={"name": "test"})
        cache = SessionCache.build(session)
        assert cache.tools == SAMPLE_TOOLS
        assert cache.server_info == {"name": "test"}

    def test_build_creates_completion_state(self) -> None:
        session = _mock_session(tools=SAMPLE_TOOLS)
        cache = SessionCache.build(session)
        assert "test_tool" in cache.completion.tool_names

    def test_build_empty_tools(self) -> None:
        session = _mock_session()
        cache = SessionCache.build(session)
        assert cache.tools == []
        assert cache.completion.tool_names == set()

    def test_build_fetches_resources_when_capable(self) -> None:
        resources = [{"uri": "file:///test.txt", "name": "test"}]
        session = _mock_session(capabilities={"resources": {}})
        session.list_resources.return_value = resources
        cache = SessionCache.build(session)
        assert cache.resources == resources
        assert "file:///test.txt" in cache.completion.resource_uris

    def test_build_fetches_prompts_when_capable(self) -> None:
        prompts = [{"name": "greet", "description": "Greeting prompt"}]
        session = _mock_session(capabilities={"prompts": {}})
        session.list_prompts.return_value = prompts
        cache = SessionCache.build(session)
        assert cache.prompts == prompts
        assert "greet" in cache.completion.prompt_names

    def test_build_skips_resources_without_capability(self) -> None:
        session = _mock_session()  # no resources capability
        cache = SessionCache.build(session)
        assert cache.resources == []
        session.list_resources.assert_not_called()

    def test_build_skips_prompts_without_capability(self) -> None:
        session = _mock_session()  # no prompts capability
        cache = SessionCache.build(session)
        assert cache.prompts == []
        session.list_prompts.assert_not_called()


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

    def test_non_list_content(self, capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
        # Result without content list should pretty-print the result dict
        _print_result({"result": {"data": "something"}})
        captured = capsys.readouterr()
        assert "something" in captured.out

    def test_content_item_not_dict(self) -> None:
        _print_result({"result": {"content": ["string item"]}})  # should not raise
