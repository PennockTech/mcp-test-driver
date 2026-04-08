# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for the REPL module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mcp_test_driver.repl import Repl, SessionCache, _print_result


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


# ---------------------------------------------------------------------------
# _cmd_help — schedule_restore_input behaviour
# ---------------------------------------------------------------------------


class TestCmdHelp:
    """/help restores the input buffer with trailing spaces intact."""

    def _make_repl(self) -> Repl:
        session = _mock_session(tools=SAMPLE_TOOLS)
        return Repl(session)

    def test_schedule_restore_called_with_original_rest(self) -> None:
        repl = self._make_repl()
        with patch("mcp_test_driver.repl.schedule_restore_input") as mock_restore:
            with patch("mcp_test_driver.repl.show_context_help_for"):
                repl._cmd_help("test_tool ")  # trailing space
        mock_restore.assert_called_once_with("test_tool ")

    def test_trailing_space_preserved_in_restore(self) -> None:
        """Trailing space must reach schedule_restore_input (not stripped early)."""
        repl = self._make_repl()
        captured = []
        with patch("mcp_test_driver.repl.schedule_restore_input", side_effect=captured.append):
            with patch("mcp_test_driver.repl.show_context_help_for"):
                repl._cmd_help("test_tool arg= ")
        assert captured == ["test_tool arg= "]

    def test_no_restore_when_rest_empty(self) -> None:
        repl = self._make_repl()
        with patch("mcp_test_driver.repl.schedule_restore_input") as mock_restore:
            repl._cmd_help("")
        mock_restore.assert_not_called()

    def test_no_restore_when_rest_whitespace_only(self) -> None:
        repl = self._make_repl()
        with patch("mcp_test_driver.repl.schedule_restore_input") as mock_restore:
            repl._cmd_help("   ")
        mock_restore.assert_not_called()

    def test_context_help_uses_stripped_rest(
        self, capsys: pytest.CaptureFixture  # type: ignore[type-arg]
    ) -> None:
        """show_context_help_for receives stripped text, not the raw original."""
        repl = self._make_repl()
        received: list[str] = []
        with patch(
            "mcp_test_driver.repl.show_context_help_for",
            side_effect=lambda state, text: received.append(text),
        ):
            with patch("mcp_test_driver.repl.schedule_restore_input"):
                repl._cmd_help("  test_tool  ")
        assert received == ["test_tool"]


# ---------------------------------------------------------------------------
# Repl startup message — libedit warning and F1/Esc-h hint
# ---------------------------------------------------------------------------


class TestReplStartup:
    """Startup banner reflects the active readline library correctly."""

    def _run_repl_once(self, rl_name: str, rl_version: str, capsys: object) -> str:
        session = _mock_session(server_info={"name": "test-srv"})
        repl = Repl(session)
        with patch("mcp_test_driver.repl.readline_info", return_value=(rl_name, rl_version)):
            with patch("mcp_test_driver.repl.setup_readline"):
                with patch("builtins.input", side_effect=EOFError):
                    repl.run()
        return capsys.readouterr().out  # type: ignore[union-attr]

    def test_libedit_warning_present(
        self, capsys: pytest.CaptureFixture  # type: ignore[type-arg]
    ) -> None:
        out = self._run_repl_once("libedit", "4.2", capsys)
        assert "libedit" in out
        assert "Warning" in out

    def test_libedit_warning_mentions_missing_features(
        self, capsys: pytest.CaptureFixture  # type: ignore[type-arg]
    ) -> None:
        out = self._run_repl_once("libedit", "4.2", capsys)
        # Both missing features should be named
        assert "tab-completion" in out.lower() or "tab" in out
        assert "F1" in out or "Esc" in out

    def test_gnu_readline_no_warning(
        self, capsys: pytest.CaptureFixture  # type: ignore[type-arg]
    ) -> None:
        out = self._run_repl_once("readline", "8.2", capsys)
        assert "Warning" not in out

    def test_gnu_readline_shows_f1_hint(
        self, capsys: pytest.CaptureFixture  # type: ignore[type-arg]
    ) -> None:
        out = self._run_repl_once("readline", "8.2", capsys)
        assert "F1" in out
        assert "Esc-h" in out

    def test_libedit_does_not_show_f1_hint_in_tab_line(
        self, capsys: pytest.CaptureFixture  # type: ignore[type-arg]
    ) -> None:
        out = self._run_repl_once("libedit", "4.2", capsys)
        lines = out.splitlines()
        # The tab-completion hint line should not mention F1/Esc-h on libedit
        tab_lines = [line for line in lines if "Tab-completion" in line]
        assert tab_lines
        for line in tab_lines:
            assert "F1" not in line
            assert "Esc-h" not in line

    def test_version_shown_in_output(
        self, capsys: pytest.CaptureFixture  # type: ignore[type-arg]
    ) -> None:
        out = self._run_repl_once("readline", "8.3", capsys)
        assert "8.3" in out

    def test_lowercase_esc_h_not_uppercase(
        self, capsys: pytest.CaptureFixture  # type: ignore[type-arg]
    ) -> None:
        out = self._run_repl_once("readline", "8.2", capsys)
        assert "Esc-h" in out
        assert "Esc-H" not in out
