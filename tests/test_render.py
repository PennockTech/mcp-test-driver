# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for result rendering."""

from __future__ import annotations

import pytest

from mcp_test_driver.render import error_name, human_size, render_response

Cap = pytest.CaptureFixture[str]


def _out(resp: object, capsys: Cap) -> str:
    render_response(resp)
    return capsys.readouterr().out


class TestToolResults:
    def test_json_text_pretty_printed(self, capsys: Cap) -> None:
        out = _out({"result": {"content": [{"type": "text", "text": '{"a": 1}'}]}}, capsys)
        assert '"a": 1' in out and "\n" in out.strip() or out.strip() == '{\n  "a": 1\n}'

    def test_plain_text(self, capsys: Cap) -> None:
        assert _out({"result": {"content": [{"type": "text", "text": "hello"}]}}, capsys).strip() == "hello"

    def test_error_result(self, capsys: Cap) -> None:
        out = _out({"result": {"isError": True, "content": [{"type": "text", "text": "boom"}]}}, capsys)
        assert "Error: boom" in out

    def test_text_is_sanitized(self, capsys: Cap) -> None:
        out = _out({"result": {"content": [{"type": "text", "text": "a\x1b[2Jb‮c"}]}}, capsys)
        assert "\x1b" not in out and "‮" not in out and "abc" in out

    def test_binary_blocks_summarised(self, capsys: Cap) -> None:
        out = _out({"result": {"content": [
            {"type": "image", "mimeType": "image/png", "data": "aGVsbG8="},
            {"type": "audio", "mimeType": "audio/wav", "data": "AAAA"},
        ]}}, capsys)
        assert "[image image/png, 5 B]" in out
        assert "[audio audio/wav, 3 B]" in out
        assert "aGVsbG8" not in out

    def test_embedded_resource_and_link(self, capsys: Cap) -> None:
        out = _out({"result": {"content": [
            {"type": "resource", "resource": {"uri": "file:///a", "mimeType": "text/plain", "text": "body"}},
            {"type": "resource", "resource": {"uri": "file:///b", "blob": "AAAA"}},
            {"type": "resource_link", "uri": "file:///c", "name": "c", "description": "see c"},
        ]}}, capsys)
        assert "file:///a" in out and "body" in out
        assert "[binary, 3 B]" in out
        assert "[link] file:///c" in out and "see c" in out

    def test_structured_content_and_extras(self, capsys: Cap) -> None:
        out = _out({"result": {
            "content": [], "structuredContent": {"n": 2}, "_meta": {"k": "v"}, "surprise": 1,
        }}, capsys)
        assert "structuredContent" in out and '"n": 2' in out
        assert "_meta" in out and "other fields" in out and "surprise" in out

    def test_annotations_shown(self, capsys: Cap) -> None:
        out = _out({"result": {"content": [{"type": "text", "text": "x", "annotations": {"audience": ["user"]}}]}}, capsys)
        assert "audience=user" in out

    def test_unknown_block_type(self, capsys: Cap) -> None:
        out = _out({"result": {"content": [{"type": "hologram", "x": 1}]}}, capsys)
        assert "[hologram content]" in out

    def test_non_dict_block(self, capsys: Cap) -> None:
        assert "string item" in _out({"result": {"content": ["string item"]}}, capsys)


class TestOtherResults:
    def test_prompt_messages(self, capsys: Cap) -> None:
        out = _out({"result": {"description": "d", "messages": [
            {"role": "user", "content": {"type": "text", "text": "hi"}},
            {"role": "assistant", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]},
        ]}}, capsys)
        assert "[user]" in out and "hi" in out and "[assistant]" in out

    def test_resource_contents(self, capsys: Cap) -> None:
        out = _out({"result": {"contents": [{"uri": "mem://x", "text": "t"}], "ttlMs": 5, "cacheScope": "public"}}, capsys)
        assert "mem://x" in out and "ttlMs=5" in out

    def test_input_required(self, capsys: Cap) -> None:
        assert "[input required]" in _out({"result": {"resultType": "input_required", "inputRequests": {}}}, capsys)

    def test_generic_result_and_non_dict(self, capsys: Cap) -> None:
        assert '"x": 1' in _out({"result": {"x": 1}}, capsys)
        assert "5" in _out({"result": 5}, capsys)
        assert _out({"result": {}}, capsys) == ""


class TestErrors:
    def test_jsonrpc_error_named(self, capsys: Cap) -> None:
        out = _out({"error": {"code": -32601, "message": "nope", "data": {"m": "x"}}}, capsys)
        assert "-32601 Method not found" in out and "nope" in out and '"m": "x"' in out

    def test_error_names(self) -> None:
        assert error_name(-32022) == "Unsupported protocol version"
        assert error_name(-32002).startswith("Resource not found")
        assert error_name(-32005) == "server error"
        assert error_name("x") == ""


def test_human_size() -> None:
    assert human_size(10) == "10 B"
    assert human_size(2048) == "2.0 KiB"
    assert human_size(3 * 1024 * 1024) == "3.0 MiB"
