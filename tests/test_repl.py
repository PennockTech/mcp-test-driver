# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for the REPL: cache, command dispatch, banner, help restore."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mcp_test_driver.protocol import McpSession
from mcp_test_driver.repl import Repl
from mcp_test_driver.transport import StdioTransport
from tests.conftest import SAMPLE_TOOLS, read_log

ReplFactory = Callable[..., Repl]


@pytest.fixture
def make_repl(fake_stdio, log_path: Path) -> Iterator[ReplFactory]:  # noqa: ANN001
    made: list[Repl] = []

    def make(scenario: dict[str, Any] | None = None, *, initialize: bool = True) -> Repl:
        sc = {"log_path": str(log_path), "tools": SAMPLE_TOOLS, **(scenario or {})}
        t = StdioTransport(fake_stdio(sc), timeout=10)
        t.trace = False
        s = McpSession(t)
        if initialize:
            s.initialize()
        r = Repl(s)
        made.append(r)
        return r

    yield make
    for r in made:
        r.session.transport.close()


class TestSessionCache:
    def test_tools_always_fetched(self, make_repl: ReplFactory) -> None:
        r = make_repl({"capabilities": {}})
        assert [t["name"] for t in r.cache.tools] == ["echo"]
        assert "echo" in r.cache.completion.tool_names
        # Answering tools/list without the capability is worth flagging.
        assert any(f.code == "tools-without-capability" for f in r.session.findings)

    def test_advertised_lists_fetched(self, make_repl: ReplFactory) -> None:
        r = make_repl({
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "resources": [{"uri": "file:///a.txt", "name": "a"}],
            "prompts": [{"name": "greet"}],
        })
        assert r.cache.resources[0]["uri"] == "file:///a.txt"
        assert r.cache.completion.resource_uris == ["file:///a.txt"]
        assert r.cache.completion.prompt_names == ["greet"]
        assert "templates" in r.cache.lists

    def test_unadvertised_lists_not_fetched_until_asked(self, make_repl: ReplFactory, log_path: Path,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl({"capabilities": {"tools": {}}, "prompts": [{"name": "p"}]})
        assert "prompts" not in r.cache.lists
        assert not any(m.get("method") == "prompts/list" for m in read_log(log_path))
        r.handle_line("/prompts")
        out = capsys.readouterr().out
        assert "does not advertise 'prompts'; asking anyway" in out
        assert "p" in out

    def test_list_changed_refreshes(self, make_repl: ReplFactory, capsys: pytest.CaptureFixture[str]) -> None:
        note = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
        r = make_repl({"tool_scripts": {"echo": [{"notify": note}, {"reply": {"content": []}}]}})
        r.handle_line("echo")
        assert "tools" in r.cache.stale
        r.before_prompt()
        assert not r.cache.stale
        assert "lists refreshed" in capsys.readouterr().out


class TestCommands:
    def test_tool_invocation_and_render(self, make_repl: ReplFactory, capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl()
        r.handle_line("echo text=hi mode=loud")
        out = capsys.readouterr().out
        assert '"text": "hi"' in out and '"mode": "loud"' in out
        assert r.last_response is not None

    def test_unknown_tool(self, make_repl: ReplFactory, capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl()
        r.handle_line("nope")
        assert "Unknown tool 'nope'" in capsys.readouterr().out

    def test_tool_called_by_real_name(self, make_repl: ReplFactory, log_path: Path) -> None:
        r = make_repl({"tools": [{"name": "two words", "inputSchema": {"type": "object"}}]})
        r.handle_line("two_words")
        calls = [m for m in read_log(log_path) if m.get("method") == "tools/call"]
        assert calls[0]["params"]["name"] == "two words"

    def test_unknown_command(self, make_repl: ReplFactory, capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl()
        assert r.handle_line("/bogus") is True
        assert "Unknown command" in capsys.readouterr().out

    def test_quit(self, make_repl: ReplFactory) -> None:
        r = make_repl()
        assert r.handle_line("/quit") is False
        assert r.handle_line("/q") is False

    def test_list_shows_title_and_hints(self, make_repl: ReplFactory, capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl({"tools": [{"name": "t", "title": "Tee", "annotations": {"readOnlyHint": True},
                                  "inputSchema": {"type": "object"}}]})
        r.handle_line("/list")
        out = capsys.readouterr().out
        assert "t (Tee)" in out and "read-only" in out

    def test_describe_shows_output_schema(self, make_repl: ReplFactory, capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl({"tools": [{"name": "t", "inputSchema": {"type": "object"},
                                  "outputSchema": {"type": "object", "properties": {"n": {"type": "number"}}}}]})
        r.handle_line("/describe t")
        assert "Output schema" in capsys.readouterr().out

    def test_prompt_arguments_are_strings(self, make_repl: ReplFactory, log_path: Path) -> None:
        r = make_repl({"prompts": [{"name": "greet"}]})
        r.handle_line("/prompt greet n=42 flag=true")
        req = [m for m in read_log(log_path) if m.get("method") == "prompts/get"][0]
        assert req["params"]["arguments"] == {"n": "42", "flag": "true"}

    def test_complete_command(self, make_repl: ReplFactory, capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl({"completions": ["alice", "bob"]})
        r.handle_line("/complete prompt greet who=a")
        assert "alice" in capsys.readouterr().out

    def test_read_resource(self, make_repl: ReplFactory, capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl({"resource_contents": {"file:///a": [{"uri": "file:///a", "text": "hello"}]}})
        r.handle_line("/read file:///a")
        assert "hello" in capsys.readouterr().out
        r.handle_line("/read file:///missing")
        assert "Resource not found" in capsys.readouterr().out

    def test_send_and_notify(self, make_repl: ReplFactory, log_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl()
        r.handle_line('/send tools/call {"name": "echo", "arguments": {"x": 1}}')
        assert '"x": 1' in capsys.readouterr().out
        r.handle_line("/notify notifications/custom a=1")
        r.handle_line("/ping")
        assert any(m.get("method") == "notifications/custom" for m in read_log(log_path))

    def test_raw_json_and_garbage(self, make_repl: ReplFactory, capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl()
        r.handle_line('/raw {"jsonrpc":"2.0","id":"r1","method":"ping"}')
        out = capsys.readouterr().out
        assert "response id=r1" in out
        r.handle_line("/raw }{ not json")
        assert "-32700" in capsys.readouterr().out

    def test_meta_and_header(self, make_repl: ReplFactory, log_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl()
        r.handle_line("/meta com.example/k=v")
        r.handle_line("/ping")
        ping = [m for m in read_log(log_path) if m.get("method") == "ping"][-1]
        assert ping["params"]["_meta"]["com.example/k"] == "v"
        r.handle_line("/meta clear")
        r.handle_line("/header X-Test: 1")
        assert "X-Test: 1" in capsys.readouterr().out
        r.handle_line("/header bad")
        assert "Usage" in capsys.readouterr().out

    def test_loglevel(self, make_repl: ReplFactory, log_path: Path) -> None:
        r = make_repl()
        r.handle_line("/loglevel warning")
        assert any(m.get("method") == "logging/setLevel" for m in read_log(log_path))
        r.session.state.era = "modern"
        r.handle_line("/loglevel debug")
        assert r.session.log_level == "debug"

    def test_roots_add_sends_list_changed(self, make_repl: ReplFactory, tmp_path: Path, log_path: Path) -> None:
        base = tmp_path / "base"
        (base / "sub").mkdir(parents=True)
        r = make_repl(initialize=False)
        r.session.enable_roots(base)
        r.session.initialize()
        r.handle_line(f"/roots add {base / 'sub'}")
        r.handle_line("/ping")
        assert any(m.get("method") == "notifications/roots/list_changed" for m in read_log(log_path))

    def test_info_and_findings(self, make_repl: ReplFactory, capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl({"tools": [{"name": "bad name", "inputSchema": {"type": "object"}}]})
        r.handle_line("/info")
        out = capsys.readouterr().out
        assert "Connection" in out and "Server capabilities" in out and "SEP-986" in out
        r.handle_line("/findings")
        assert "SEP-986" in capsys.readouterr().out
        r.handle_line("/last")

    def test_trace_toggle(self, make_repl: ReplFactory) -> None:
        r = make_repl()
        r.handle_line("/trace on")
        assert r.session.transport.trace
        r.handle_line("/trace")
        assert not r.session.transport.trace

    def test_reconnect_relaunches(self, make_repl: ReplFactory) -> None:
        r = make_repl()
        pid = r.session.transport.pid  # type: ignore[attr-defined]
        r.handle_line("/reconnect")
        assert r.session.transport.pid != pid  # type: ignore[attr-defined]
        assert r.session.state.initialized


class TestCmdHelp:
    """/help restores the input buffer (F1 macro) with trailing spaces intact."""

    def test_restore_with_original_rest(self, make_repl: ReplFactory) -> None:
        r = make_repl()
        with patch("mcp_test_driver.completion.schedule_restore_input") as restore:
            r.handle_line("/help echo arg= ")
        restore.assert_called_once_with("echo arg= ")

    def test_no_restore_when_empty(self, make_repl: ReplFactory) -> None:
        r = make_repl()
        with patch("mcp_test_driver.completion.schedule_restore_input") as restore:
            r.handle_line("/help")
            r.handle_line("/help    ")
        restore.assert_not_called()

    def test_context_help(self, make_repl: ReplFactory, capsys: pytest.CaptureFixture[str]) -> None:
        r = make_repl()
        with patch("mcp_test_driver.completion.schedule_restore_input"):
            r.handle_line("/help echo mode=")
            assert "enum: loud, quiet" in capsys.readouterr().out
            r.handle_line("/help /describe")
            assert "/describe" in capsys.readouterr().out


class TestReplStartup:
    """Startup banner reflects the active readline library correctly."""

    @pytest.fixture
    def run_once(self, make_repl: ReplFactory, capsys: pytest.CaptureFixture[str]):  # noqa: ANN201
        def run(rl_name: str, rl_version: str) -> str:
            repl = make_repl({"server_info": {"name": "test-srv", "version": "1"}})
            capsys.readouterr()
            with patch("mcp_test_driver.repl.readline_info", return_value=(rl_name, rl_version)):
                with patch("mcp_test_driver.repl.setup_readline"):
                    with patch("builtins.input", side_effect=EOFError):
                        repl.run()
            return capsys.readouterr().out

        return run

    def test_libedit_warning(self, run_once) -> None:  # noqa: ANN001
        out = run_once("libedit", "4.2")
        assert "libedit" in out and "Warning" in out
        assert "tab-completion" in out.lower() and ("F1" in out or "Esc" in out)

    def test_gnu_readline(self, run_once) -> None:  # noqa: ANN001
        out = run_once("readline", "8.3")
        assert "Warning" not in out
        assert "F1" in out and "Esc-h" in out and "Esc-H" not in out
        assert "8.3" in out and "test-srv" in out

    def test_libedit_tab_line_has_no_f1_hint(self, run_once) -> None:  # noqa: ANN001
        out = run_once("libedit", "4.2")
        tab_lines = [line for line in out.splitlines() if "Tab-completion" in line]
        assert tab_lines and all("F1" not in line and "Esc-h" not in line for line in tab_lines)
