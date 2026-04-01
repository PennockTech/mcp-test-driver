# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for MCP protocol layer."""

from __future__ import annotations

import pytest

from mcp_test_driver.protocol import McpError, McpSession, _check_error


class FakeTransport:
    """Fake transport that returns canned responses."""

    def __init__(self, responses: list[dict | None]) -> None:
        self._responses = list(responses)
        self.sent: list[dict] = []
        self.notifications: list[dict] = []
        self.trace = False
        self.handler_registry = None  # set by McpSession
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


def _server_info_response(
    name: str = "test-server",
    version: str = "1.0",
    *,
    capabilities: dict | None = None,
) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": capabilities or {},
            "serverInfo": {"name": name, "version": version},
        },
    }


def _tools_response(tools: list[dict] | None = None, *, req_id: int = 2) -> dict:
    if tools is None:
        tools = [
            {
                "name": "test_tool",
                "description": "A test tool",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
    return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}


class TestMcpSession:
    """Tests for McpSession."""

    def test_initialize_sends_correct_request(self) -> None:
        transport = FakeTransport([_server_info_response()])
        session = McpSession(transport)
        session.initialize()

        req = transport.sent[0]
        assert req["method"] == "initialize"
        params = req["params"]
        assert params["protocolVersion"] == "2024-11-05"
        assert params["clientInfo"]["name"] == "mcp-test-driver"

    def test_initialize_sends_notification(self) -> None:
        transport = FakeTransport([_server_info_response()])
        session = McpSession(transport)
        session.initialize()

        assert len(transport.notifications) == 1
        assert transport.notifications[0]["method"] == "notifications/initialized"

    def test_initialize_stores_server_info(self) -> None:
        transport = FakeTransport([_server_info_response("my-server", "2.0")])
        session = McpSession(transport)
        session.initialize()

        assert session.server_info["name"] == "my-server"
        assert session.server_info["version"] == "2.0"

    def test_initialize_stores_capabilities(self) -> None:
        caps = {"resources": {}, "prompts": {}, "tools": {}}
        transport = FakeTransport([_server_info_response(capabilities=caps)])
        session = McpSession(transport)
        session.initialize()

        assert session.server_capabilities == caps

    def test_initialize_raises_on_none(self) -> None:
        transport = FakeTransport([None])
        session = McpSession(transport)
        with pytest.raises(ConnectionError):
            session.initialize()

    def test_list_tools_returns_tools(self) -> None:
        transport = FakeTransport([_tools_response()])
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
        transport = FakeTransport(
            [
                _server_info_response(),
                _tools_response(),
            ]
        )
        session = McpSession(transport)
        session.initialize()
        session.list_tools()

        assert transport.sent[0]["id"] == 1
        assert transport.sent[1]["id"] == 2

    def test_reconnect_resets_id_and_calls_transport(self) -> None:
        transport = FakeTransport(
            [
                _server_info_response(),
                _tools_response(),
            ]
        )
        session = McpSession(transport)
        session._id_seq = 42

        tools = session.reconnect()
        assert transport._reconnect_count == 1
        # ID sequence should be reset; after reconnect it does initialize (id=1)
        # and list_tools (id=2)
        assert transport.sent[0]["id"] == 1
        assert transport.sent[1]["id"] == 2
        assert len(tools) == 1


class TestCheckError:
    """Tests for JSON-RPC error response detection."""

    def test_no_error_field(self) -> None:
        _check_error({"jsonrpc": "2.0", "id": 1, "result": {}})

    def test_error_field_raises(self) -> None:
        resp = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
        with pytest.raises(McpError) as exc_info:
            _check_error(resp)
        assert exc_info.value.code == -32600
        assert "Invalid Request" in str(exc_info.value)

    def test_error_with_data(self) -> None:
        resp = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32603,
                "message": "Internal error",
                "data": "details here",
            },
        }
        with pytest.raises(McpError) as exc_info:
            _check_error(resp)
        assert exc_info.value.data == "details here"

    def test_error_field_non_dict_ignored(self) -> None:
        # error field is present but not a dict — not a real error
        _check_error({"jsonrpc": "2.0", "id": 1, "error": "string"})


class TestMcpSessionRobustness:
    """Tests for protocol-level error handling."""

    def test_initialize_with_error_response(self) -> None:
        resp = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "Bad request"},
        }
        transport = FakeTransport([resp])
        session = McpSession(transport)
        with pytest.raises(McpError, match="Bad request"):
            session.initialize()

    def test_list_tools_with_error_response(self) -> None:
        resp = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        }
        transport = FakeTransport([resp])
        session = McpSession(transport)
        with pytest.raises(McpError, match="Method not found"):
            session.list_tools()

    def test_initialize_with_missing_result(self) -> None:
        resp = {"jsonrpc": "2.0", "id": 1}
        transport = FakeTransport([resp])
        session = McpSession(transport)
        with pytest.raises(ConnectionError, match="invalid initialize response"):
            session.initialize()

    def test_list_tools_with_empty_result(self) -> None:
        resp = {"jsonrpc": "2.0", "id": 1, "result": {}}
        transport = FakeTransport([resp])
        session = McpSession(transport)
        tools = session.list_tools()
        assert tools == []

    def test_list_tools_with_non_list_tools(self) -> None:
        resp = {"jsonrpc": "2.0", "id": 1, "result": {"tools": "not a list"}}
        transport = FakeTransport([resp])
        session = McpSession(transport)
        tools = session.list_tools()
        assert tools == []


class TestResourceMethods:
    """Tests for resource-related protocol methods."""

    def test_list_resources(self) -> None:
        resources = [{"uri": "file:///test.txt", "name": "test"}]
        resp = {"jsonrpc": "2.0", "id": 1, "result": {"resources": resources}}
        transport = FakeTransport([resp])
        session = McpSession(transport)
        result = session.list_resources()
        assert result == resources

    def test_list_resources_pagination(self) -> None:
        page1 = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "resources": [{"uri": "a"}],
                "nextCursor": "page2",
            },
        }
        page2 = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "resources": [{"uri": "b"}],
            },
        }
        transport = FakeTransport([page1, page2])
        session = McpSession(transport)
        result = session.list_resources()
        assert len(result) == 2

    def test_list_resource_templates(self) -> None:
        templates = [{"uriTemplate": "file:///{path}", "name": "files"}]
        resp = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"resourceTemplates": templates},
        }
        transport = FakeTransport([resp])
        session = McpSession(transport)
        result = session.list_resource_templates()
        assert result == templates

    def test_read_resource(self) -> None:
        resp = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "contents": [{"uri": "file:///test.txt", "text": "hello"}],
            },
        }
        transport = FakeTransport([resp])
        session = McpSession(transport)
        result = session.read_resource("file:///test.txt")
        assert result is not None
        req = transport.sent[0]
        assert req["params"]["uri"] == "file:///test.txt"


class TestPromptMethods:
    """Tests for prompt-related protocol methods."""

    def test_list_prompts(self) -> None:
        prompts = [{"name": "greet", "description": "Greeting"}]
        resp = {"jsonrpc": "2.0", "id": 1, "result": {"prompts": prompts}}
        transport = FakeTransport([resp])
        session = McpSession(transport)
        result = session.list_prompts()
        assert result == prompts

    def test_list_prompts_pagination(self) -> None:
        page1 = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "prompts": [{"name": "a"}],
                "nextCursor": "next",
            },
        }
        page2 = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "prompts": [{"name": "b"}],
            },
        }
        transport = FakeTransport([page1, page2])
        session = McpSession(transport)
        result = session.list_prompts()
        assert len(result) == 2

    def test_get_prompt_no_args(self) -> None:
        resp = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": "Hi"}}
                ],
            },
        }
        transport = FakeTransport([resp])
        session = McpSession(transport)
        result = session.get_prompt("greet")
        assert result is not None
        req = transport.sent[0]
        assert req["params"]["name"] == "greet"
        assert "arguments" not in req["params"]

    def test_get_prompt_with_args(self) -> None:
        resp = {"jsonrpc": "2.0", "id": 1, "result": {"messages": []}}
        transport = FakeTransport([resp])
        session = McpSession(transport)
        session.get_prompt("greet", {"name": "Alice"})
        req = transport.sent[0]
        assert req["params"]["arguments"] == {"name": "Alice"}


class TestUtilityMethods:
    """Tests for ping, logging, and completion."""

    def test_ping(self) -> None:
        resp = {"jsonrpc": "2.0", "id": 1, "result": {}}
        transport = FakeTransport([resp])
        session = McpSession(transport)
        result = session.ping()
        assert transport.sent[0]["method"] == "ping"
        assert result is not None

    def test_set_log_level(self) -> None:
        resp = {"jsonrpc": "2.0", "id": 1, "result": {}}
        transport = FakeTransport([resp])
        session = McpSession(transport)
        session.set_log_level("debug")
        req = transport.sent[0]
        assert req["method"] == "logging/setLevel"
        assert req["params"]["level"] == "debug"

    def test_complete(self) -> None:
        resp = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "completion": {
                    "values": ["opt1", "opt2"],
                    "hasMore": False,
                    "total": 2,
                }
            },
        }
        transport = FakeTransport([resp])
        session = McpSession(transport)
        values = session.complete("ref/prompt", "greet", "name", "Al")
        assert values == ["opt1", "opt2"]
        req = transport.sent[0]
        assert req["method"] == "completion/complete"

    def test_complete_empty_result(self) -> None:
        resp = {"jsonrpc": "2.0", "id": 1, "result": {}}
        transport = FakeTransport([resp])
        session = McpSession(transport)
        values = session.complete("ref/prompt", "greet", "name", "")
        assert values == []
