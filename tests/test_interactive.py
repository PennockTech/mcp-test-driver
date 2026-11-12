# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for interactive elicitation and sampling handlers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_test_driver.handlers import HandlerError
from mcp_test_driver.interactive import ElicitationHandler, SamplingHandler
from mcp_test_driver.protocol import McpSession
from mcp_test_driver.transport import StdioTransport


class Scripted:
    """A Prompter that replays answers and records prompts."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


FORM = {
    "message": "Tell me about you",
    "requestedSchema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "title": "Name", "minLength": 2},
            "age": {"type": "integer", "minimum": 0},
            "subscribe": {"type": "boolean", "default": False},
            "colour": {"type": "string", "enum": ["r", "g"], "enumNames": ["Red", "Green"]},
            "size": {"type": "string", "oneOf": [{"const": "s", "title": "Small"}, {"const": "l", "title": "Large"}]},
            "tags": {"type": "array", "items": {"type": "string", "enum": ["a", "b", "c"]}},
            "note": {"type": "string"},
        },
        "required": ["name", "age"],
    },
}


class TestElicitationForm:
    def test_full_form(self) -> None:
        p = Scripted("a", "x", "Bo", "-1", "30", "", "2", "l", "1,c", "")
        result = ElicitationHandler(p).handle(FORM)
        assert result == {
            "action": "accept",
            "content": {"name": "Bo", "age": 30, "subscribe": False, "colour": "g", "size": "l", "tags": ["a", "c"]},
        }

    def test_required_reprompts(self, capsys: pytest.CaptureFixture[str]) -> None:
        p = Scripted("a", "", "Bob", "5", "", "", "", "", "")
        ElicitationHandler(p).handle(FORM)
        assert "required" in capsys.readouterr().out

    @pytest.mark.parametrize("first,action", [("d", "decline"), ("c", "cancel")])
    def test_decline_or_cancel_up_front(self, first: str, action: str) -> None:
        assert ElicitationHandler(Scripted(first)).handle(FORM) == {"action": action}

    def test_escape_mid_form(self) -> None:
        assert ElicitationHandler(Scripted("a", "!cancel")).handle(FORM) == {"action": "cancel"}

    def test_eof_cancels(self) -> None:
        assert ElicitationHandler(Scripted()).handle(FORM) == {"action": "cancel"}

    def test_message_is_sanitized(self, capsys: pytest.CaptureFixture[str]) -> None:
        ElicitationHandler(Scripted("d")).handle({"message": "hi\x1b[2J‮", "requestedSchema": {}})
        out = capsys.readouterr().out
        assert "\x1b[2J" not in out and "‮" not in out

    def test_undeclared_mode_rejected(self) -> None:
        with pytest.raises(HandlerError) as ei:
            ElicitationHandler(Scripted()).handle({"mode": "url", "url": "https://x"})
        assert ei.value.code == -32602


class TestElicitationUrl:
    def test_accept_shows_host(self, capsys: pytest.CaptureFixture[str]) -> None:
        h = ElicitationHandler(Scripted("a"), frozenset({"form", "url"}))
        result = h.handle({"mode": "url", "message": "Log in", "url": "https://auth.example.com/x?y=1", "elicitationId": "e1"})
        assert result == {"action": "accept"}
        out = capsys.readouterr().out
        assert "auth.example.com" in out and "never opens URLs" in out

    def test_default_is_decline(self) -> None:
        h = ElicitationHandler(Scripted("?"), frozenset({"url"}))
        assert h.handle({"mode": "url", "url": "https://x"}) == {"action": "decline"}


def test_capabilities() -> None:
    assert ElicitationHandler(Scripted()).capability == ("elicitation", {})
    assert ElicitationHandler(Scripted(), frozenset({"form", "url"})).capability == (
        "elicitation", {"form": {}, "url": {}})
    assert SamplingHandler(Scripted()).capability == ("sampling", {})


class TestSampling:
    PARAMS = {
        "systemPrompt": "be brief",
        "messages": [{"role": "user", "content": {"type": "text", "text": "capital of France?"}}],
        "maxTokens": 10,
    }

    def test_text_reply(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = SamplingHandler(Scripted("Paris")).handle(self.PARAMS)
        assert result["content"] == {"type": "text", "text": "Paris"}
        assert result["role"] == "assistant" and result["stopReason"] == "endTurn"
        assert "capital of France?" in capsys.readouterr().out

    def test_json_reply(self) -> None:
        typed = {"role": "assistant", "content": {"type": "tool_use", "id": "1", "name": "f", "input": {}},
                 "model": "m", "stopReason": "toolUse"}
        assert SamplingHandler(Scripted("!json " + json.dumps(typed))).handle(self.PARAMS) == typed

    def test_decline(self) -> None:
        with pytest.raises(HandlerError) as ei:
            SamplingHandler(Scripted("!decline")).handle(self.PARAMS)
        assert ei.value.code == -1

    def test_bad_json(self) -> None:
        with pytest.raises(HandlerError):
            SamplingHandler(Scripted("!json {")).handle(self.PARAMS)


class TestEndToEnd:
    def test_elicitation_over_stdio(self, fake_stdio, tmp_path: Path) -> None:  # noqa: ANN001
        ask = {"method": "elicitation/create", "params": {"message": "Name?", "requestedSchema": {
            "type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}
        cmd = fake_stdio({"tools": [{"name": "ask", "inputSchema": {"type": "object"}}],
                          "tool_scripts": {"ask": [{"request": ask}, {"reply": {}}]}})
        t = StdioTransport(cmd, timeout=10)
        t.trace = False
        s = McpSession(t)
        s.enable_elicitation(Scripted("a", "Ada"))
        s.initialize()
        assert s.state.client_capabilities == {"elicitation": {}}
        resp = s.call_tool("ask", {})
        answers = json.loads(resp["result"]["content"][0]["text"])
        assert answers[0]["result"] == {"action": "accept", "content": {"name": "Ada"}}
        t.close()

    def test_sampling_decline_is_error_reply(self, fake_stdio) -> None:  # noqa: ANN001
        ask = {"method": "sampling/createMessage", "params": {"messages": [], "maxTokens": 5}}
        cmd = fake_stdio({"tools": [{"name": "ask", "inputSchema": {"type": "object"}}],
                          "tool_scripts": {"ask": [{"request": ask}, {"reply": {}}]}})
        t = StdioTransport(cmd, timeout=10)
        t.trace = False
        s = McpSession(t)
        s.enable_sampling(Scripted("!decline"))
        s.initialize()
        answers = json.loads(s.call_tool("ask", {})["result"]["content"][0]["text"])
        assert answers[0]["error"]["code"] == -1
        t.close()


def test_url_elicitation_required_rendering(capsys: pytest.CaptureFixture[str]) -> None:
    from mcp_test_driver.render import render_error

    render_error({"code": -32042, "message": "need auth", "data": {"elicitations": [
        {"mode": "url", "url": "https://x.example/login", "message": "Log in", "elicitationId": "e"}]}})
    out = capsys.readouterr().out
    assert "URL elicitation required" in out and "https://x.example/login" in out
