# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for the deprecated 2024-11-05 HTTP+SSE transport and fallback to it."""

from __future__ import annotations

import json

import pytest

from mcp_test_driver.negotiate import ConnectOptions, connect
from mcp_test_driver.protocol import McpSession
from mcp_test_driver.transport import HttpTransport, TransportError
from mcp_test_driver.transport.legacy_sse import LegacySseTransport
from tests.conftest import SAMPLE_TOOLS

OLD = {"legacy_sse": True, "versions": ["2024-11-05"], "tools": SAMPLE_TOOLS}


def test_direct(fake_http) -> None:  # noqa: ANN001
    srv = fake_http(OLD)
    t = LegacySseTransport(srv.url, timeout=10)
    t.trace = False
    assert t.endpoint == srv.url.replace("/mcp", "/messages?sid=1")
    s = McpSession(t)
    s.initialize()
    assert s.state.version == "2024-11-05"
    resp = s.call_tool("echo", {"text": "old"})
    assert json.loads(resp["result"]["content"][0]["text"]) == {"text": "old"}
    t.close()


def test_auto_fallback_from_streamable_http(fake_http) -> None:  # noqa: ANN001
    srv = fake_http(OLD)
    t = HttpTransport(srv.url, timeout=10)
    t.trace = False
    s = McpSession(t)
    connect(s, ConnectOptions(probe_timeout=2))
    assert s.transport.kind == "sse"
    assert any("HTTP+SSE" in h for h in s.state.how_chosen)
    assert any(f.code == "legacy-sse-transport" for f in s.findings)
    assert [x["name"] for x in s.list_tools()] == ["echo"]
    s.transport.close()


def test_server_request_over_legacy_sse(fake_http, tmp_path) -> None:  # noqa: ANN001
    srv = fake_http({**OLD, "tool_scripts": {"echo": [{"request": {"method": "roots/list"}}, {"reply": {}}]}})
    t = LegacySseTransport(srv.url, timeout=10)
    t.trace = False
    s = McpSession(t)
    s.enable_roots(tmp_path)
    s.initialize()
    answers = json.loads(s.call_tool("echo", {})["result"]["content"][0]["text"])
    assert answers[0]["result"]["roots"]
    t.close()


def test_cross_origin_endpoint_refused(fake_http) -> None:  # noqa: ANN001
    srv = fake_http({**OLD, "endpoint": "http://evil.example/steal"})
    with pytest.raises(TransportError, match="another origin"):
        LegacySseTransport(srv.url, timeout=5)


def test_not_an_sse_server(fake_http) -> None:  # noqa: ANN001
    srv = fake_http({"tools": SAMPLE_TOOLS})  # GET → 405
    with pytest.raises(TransportError, match="405"):
        LegacySseTransport(srv.url, timeout=5)
