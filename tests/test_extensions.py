# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for tasks (both generations), MCP Apps, skills and declared extensions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from mcp_test_driver.negotiate import ConnectOptions, connect
from mcp_test_driver.protocol import McpSession
from mcp_test_driver.repl import Repl
from mcp_test_driver.tasks import TASKS_EXTENSION, task_in
from mcp_test_driver.transport import StdioTransport
from tests.conftest import SAMPLE_TOOLS, read_log

ELICIT = {"ok": {"method": "elicitation/create", "params": {
    "message": "Proceed?", "requestedSchema": {"type": "object", "properties": {"go": {"type": "boolean"}},
                                               "required": ["go"]}}}}

Factory = Callable[..., Repl]


@pytest.fixture
def make(fake_stdio, log_path: Path) -> Iterator[Factory]:  # noqa: ANN001
    made: list[Repl] = []

    def build(scenario: dict[str, Any], era: str = "auto") -> Repl:
        t = StdioTransport(fake_stdio({"log_path": str(log_path), "tools": SAMPLE_TOOLS, **scenario}), timeout=10)
        t.trace = False
        s = McpSession(t)
        connect(s, ConnectOptions(era=era, probe_timeout=1))  # type: ignore[arg-type]
        r = Repl(s)
        made.append(r)
        return r

    yield build
    for r in made:
        r.session.transport.close()


def _methods(log_path: Path) -> list[str]:
    return [m.get("method") for m in read_log(log_path) if isinstance(m, dict) and m.get("method")]


class TestCoreTasks:
    SCEN = {"versions": ["2025-11-25"], "capabilities": {"tools": {}, "tasks": {"list": {}, "requests": {"tools": {"call": {}}}}},
            "tool_scripts": {"echo": [{"task": {"polls": 2, "result": {"content": [{"type": "text", "text": "finished"}]}}}]}}

    def test_task_followed_to_result(self, make: Factory, log_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        r = make(self.SCEN)
        r.handle_line("/task echo text=hi")
        assert "finished" in capsys.readouterr().out
        sent = [m for m in read_log(log_path) if isinstance(m, dict) and m.get("method") == "tools/call"]
        assert sent[0]["params"]["task"] == {}
        assert _methods(log_path).count("tasks/get") >= 2 and "tasks/result" in _methods(log_path)

    def test_list_get_cancel(self, make: Factory, capsys: pytest.CaptureFixture[str]) -> None:
        r = make(self.SCEN)
        r.follow_tasks = False
        r.handle_line("/task echo")
        r.handle_line("/tasks list")
        out = capsys.readouterr().out
        assert "task-1" in out
        r.handle_line("/tasks cancel task-1")
        assert "cancelled" in capsys.readouterr().out
        r.handle_line("/tasks cancel task-1")
        assert "already finished" in capsys.readouterr().out


class TestExtensionTasks:
    SCEN = {"mode": "modern", "versions": ["2026-07-28"],
            "tool_scripts": {"echo": [{"task": {"polls": 1, "input": ELICIT}}]}}

    def test_task_with_input_required(self, make: Factory, log_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        r = make(self.SCEN)
        r.session.enable_auto_elicitation()
        r.handle_line("/task echo")
        assert TASKS_EXTENSION in r.session.extensions
        calls = [m for m in read_log(log_path) if isinstance(m, dict) and m.get("method") == "tools/call"]
        meta = calls[0]["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"]
        assert TASKS_EXTENSION in meta["extensions"]
        update = [m for m in read_log(log_path) if isinstance(m, dict) and m.get("method") == "tasks/update"][0]
        assert update["params"]["inputResponses"] == {"ok": {"action": "accept", "content": {"go": True}}}
        assert '"go": true' in capsys.readouterr().out

    def test_unsolicited_task_handle_followed(self, make: Factory, capsys: pytest.CaptureFixture[str]) -> None:
        scen = {**self.SCEN, "tool_scripts": {"echo": [{"task": {"polls": 1}}]}}
        r = make(scen)
        r.handle_line("echo text=x")
        assert "done" in capsys.readouterr().out

    def test_task_in(self) -> None:
        assert task_in({"resultType": "task", "taskId": "t", "status": "working"}).generation == "extension"  # type: ignore[union-attr]
        assert task_in({"task": {"taskId": "t", "status": "working"}}).generation == "core"  # type: ignore[union-attr]
        assert task_in({"content": []}) is None


class TestApps:
    UI = "ui://demo/view"
    SCEN = {"tools": [{"name": "chart", "inputSchema": {"type": "object"}, "_meta": {"ui": {"resourceUri": UI}}}],
            "resource_contents": {UI: [{"uri": UI, "mimeType": "text/html;profile=mcp-app", "text": "<html>hi</html>"}],
                                  "ui://demo/bad": [{"uri": "ui://demo/bad", "mimeType": "text/plain", "text": "x"}]}}

    def test_list_and_save(self, make: Factory, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        r = make(self.SCEN)
        r.handle_line("/app")
        assert "chart" in capsys.readouterr().out
        out = tmp_path / "chart.html"
        r.handle_line(f"/app save chart {out}")
        assert out.read_text() == "<html>hi</html>"
        r.handle_line(f"/app save ui://demo/bad {tmp_path / 'b.html'}")
        assert any(f.code == "app-mime" for f in r.session.findings)


class TestSkills:
    def _scenario(self, text: str, listed: str) -> dict[str, Any]:
        uri = "skill://git-workflow/SKILL.md"
        digest = "sha256:" + hashlib.sha256(listed.encode()).hexdigest()
        return {"skills": [{"uri": uri, "frontmatter": {"name": "git-workflow", "description": "Git rules"},
                            "resources": [{"uri": uri, "digest": digest, "size": len(listed)}]}],
                "resource_contents": {uri: [{"uri": uri, "mimeType": "text/markdown", "text": text}]}}

    def test_list_get_read_verified(self, make: Factory, capsys: pytest.CaptureFixture[str]) -> None:
        r = make(self._scenario("# skill", "# skill"))
        r.handle_line("/skills")
        assert "git-workflow" in capsys.readouterr().out
        r.handle_line("/skills get skill://git-workflow/SKILL.md")
        r.handle_line("/skills read skill://git-workflow/SKILL.md")
        assert "digest verified" in capsys.readouterr().out

    def test_digest_mismatch(self, make: Factory) -> None:
        r = make(self._scenario("# tampered", "# skill"))
        r.handle_line("/skills")
        r.handle_line("/skills read skill://git-workflow/SKILL.md")
        assert any(f.code == "skill-digest-mismatch" for f in r.session.findings)


def test_extension_command_and_report(make: Factory, capsys: pytest.CaptureFixture[str]) -> None:
    r = make({"capabilities": {"tools": {}, "extensions": {"io.modelcontextprotocol/ui": {"v": 1}}}}, era="legacy")
    r.handle_line("/extension add com.example/thing {x: 1}")
    assert r.session.client_capabilities()["extensions"] == {"com.example/thing": {"x": 1}}
    out = capsys.readouterr().out
    assert "io.modelcontextprotocol/ui" in out
    r.handle_line("/extension remove com.example/thing")
    assert "extensions" not in r.session.client_capabilities()
    from mcp_test_driver.report import print_report

    print_report(r.session, r.cache)
    assert "Server extensions" in capsys.readouterr().out


def test_cli_extension_flag(fake_stdio) -> None:  # noqa: ANN001
    import subprocess
    import sys

    log = Path(fake_stdio({})[-1]).with_name("ext.jsonl")
    cmd = fake_stdio({"tools": SAMPLE_TOOLS, "log_path": str(log)})
    r = subprocess.run([sys.executable, "-m", "mcp_test_driver", "-q", "--no-trace", "--era", "legacy",
                        "--extension", 'com.example/x={"a": 1}', *cmd], input="", capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    init = json.loads(log.read_text().splitlines()[0])
    assert init["params"]["capabilities"]["extensions"] == {"com.example/x": {"a": 1}}
