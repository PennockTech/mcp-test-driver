# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for MCP protocol layer."""

from __future__ import annotations

import pytest

from mcp_test_driver.protocol import McpSession


class FakeTransport:
    """Fake transport that returns canned responses."""

    def __init__(self, responses: list[dict | None]) -> None:
        self._responses = list(responses)
        self.sent: list[dict] = []
        self.notifications: list[dict] = []
        self.trace = False
        self._closed = False
        self._reconnect_count = 0

    def request(self, obj: dict) -> dict | None:
        self.sent.append(obj)
        if self._responses:
            return self._responses.pop(0)
        return None

    def notify(self, obj: dict) -> None:
        self.notifications.append(obj)

    def close(self) -> None:
        self._closed = True

    def reconnect(self) -> None:
        self._reconnect_count += 1


class TestMcpSession:
    """Tests for McpSession."""

    def _server_info_response(
        self, name: str = "test-server", version: str = "1.0"
    ) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": name, "version": version},
            },
        }

    def _tools_response(self, tools: list[dict] | None = None) -> dict:
        if tools is None:
            tools = [
                {
                    "name": "test_tool",
                    "description": "A test tool",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]
        return {"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}}

    def test_initialize_sends_correct_request(self) -> None:
        transport = FakeTransport([self._server_info_response()])
        session = McpSession(transport)
        session.initialize()

        req = transport.sent[0]
        assert req["method"] == "initialize"
        params = req["params"]
        assert params["protocolVersion"] == "2024-11-05"
        assert params["clientInfo"]["name"] == "mcp-test-driver"

    def test_initialize_sends_notification(self) -> None:
        transport = FakeTransport([self._server_info_response()])
        session = McpSession(transport)
        session.initialize()

        assert len(transport.notifications) == 1
        assert transport.notifications[0]["method"] == "notifications/initialized"

    def test_initialize_stores_server_info(self) -> None:
        transport = FakeTransport(
            [self._server_info_response("my-server", "2.0")]
        )
        session = McpSession(transport)
        session.initialize()

        assert session.server_info["name"] == "my-server"
        assert session.server_info["version"] == "2.0"

    def test_initialize_raises_on_none(self) -> None:
        transport = FakeTransport([None])
        session = McpSession(transport)
        with pytest.raises(ConnectionError):
            session.initialize()

    def test_list_tools_returns_tools(self) -> None:
        transport = FakeTransport([self._tools_response()])
        session = McpSession(transport)
        # Skip initialize, go straight to list_tools
        session._id_seq = 1
        tools = session.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "test_tool"

    def test_list_tools_raises_on_none(self) -> None:
        transport = FakeTransport([None])
        session = McpSession(transport)
        with pytest.raises(ConnectionError):
            session.list_tools()

    def test_call_tool_sends_correct_request(self) -> None:
        resp = {"jsonrpc": "2.0", "id": 1, "result": {"content": []}}
        transport = FakeTransport([resp])
        session = McpSession(transport)
        result = session.call_tool("test_tool", {"key": "val"})

        req = transport.sent[0]
        assert req["method"] == "tools/call"
        assert req["params"]["name"] == "test_tool"
        assert req["params"]["arguments"] == {"key": "val"}
        assert result is not None

    def test_call_tool_returns_none_on_disconnect(self) -> None:
        transport = FakeTransport([None])
        session = McpSession(transport)
        result = session.call_tool("test_tool", {})
        assert result is None

    def test_id_sequence_increments(self) -> None:
        transport = FakeTransport([
            self._server_info_response(),
            self._tools_response(),
        ])
        session = McpSession(transport)
        session.initialize()
        session.list_tools()

        assert transport.sent[0]["id"] == 1
        assert transport.sent[1]["id"] == 2

    def test_reconnect_resets_id_and_calls_transport(self) -> None:
        transport = FakeTransport([
            self._server_info_response(),
            self._tools_response(),
        ])
        session = McpSession(transport)
        session._id_seq = 42

        tools = session.reconnect()
        assert transport._reconnect_count == 1
        # ID sequence should be reset; after reconnect it does initialize (id=1)
        # and list_tools (id=2)
        assert transport.sent[0]["id"] == 1
        assert transport.sent[1]["id"] == 2
        assert len(tools) == 1
