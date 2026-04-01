# Copyright 2026 Phil Pennock — see LICENSE file.

"""MCP JSON-RPC protocol layer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .color import bold_err, eprint

if TYPE_CHECKING:
    from .transport import Transport


class McpSession:
    """Manages an MCP session over a transport."""

    PROTOCOL_VERSION = "2024-11-05"
    CLIENT_NAME = "mcp-test-driver"
    CLIENT_VERSION = "0.1.0"

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self._id_seq = 0
        self.server_info: dict[str, object] = {}

    def _next_id(self) -> int:
        self._id_seq += 1
        return self._id_seq

    def initialize(self) -> dict[str, object]:
        """Perform MCP initialize handshake."""
        resp = self.transport.request(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": self.CLIENT_NAME,
                        "version": self.CLIENT_VERSION,
                    },
                },
            }
        )
        if resp is None:
            raise ConnectionError("Server closed connection during initialize")
        result = resp.get("result", {})
        assert isinstance(result, dict)
        info = result.get("serverInfo", {})
        assert isinstance(info, dict)
        self.server_info = info
        eprint(
            bold_err(f"Connected: {info.get('name', '?')} {info.get('version', '')}")
        )
        self.transport.notify(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        return resp

    def list_tools(self) -> list[dict[str, object]]:
        """Fetch the list of available tools."""
        resp = self.transport.request(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": {},
            }
        )
        if resp is None:
            raise ConnectionError("Server closed connection during tools/list")
        result = resp.get("result", {})
        assert isinstance(result, dict)
        tools = result.get("tools", [])
        assert isinstance(tools, list)
        return tools  # type: ignore[return-value]

    def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> dict[str, object] | None:
        """Invoke a tool by name with the given arguments."""
        return self.transport.request(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )

    def reconnect(self) -> list[dict[str, object]]:
        """Reconnect transport, re-initialize, and return fresh tools list."""
        self._id_seq = 0
        self.transport.reconnect()
        self.initialize()
        return self.list_tools()
