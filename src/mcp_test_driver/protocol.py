# Copyright 2026 Phil Pennock — see LICENSE file.

"""MCP JSON-RPC protocol layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .color import bold_err, eprint, red

if TYPE_CHECKING:
    from .transport import Transport


class McpError(Exception):
    """Raised when the MCP server returns a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"MCP error {code}: {message}")


def _check_error(resp: dict[str, Any]) -> None:
    """Raise McpError if the response contains a JSON-RPC error."""
    error = resp.get("error")
    if error and isinstance(error, dict):
        raise McpError(
            code=error.get("code", -1),
            message=error.get("message", "Unknown error"),
            data=error.get("data"),
        )


class McpSession:
    """Manages an MCP session over a transport."""

    PROTOCOL_VERSION = "2024-11-05"
    CLIENT_NAME = "mcp-test-driver"
    CLIENT_VERSION = "0.1.0"

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self._id_seq = 0
        self.server_info: dict[str, Any] = {}

    def _next_id(self) -> int:
        self._id_seq += 1
        return self._id_seq

    def initialize(self) -> dict[str, Any]:
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
        _check_error(resp)
        result = resp.get("result")
        if not isinstance(result, dict):
            raise ConnectionError("Server returned invalid initialize response")
        info = result.get("serverInfo", {})
        if isinstance(info, dict):
            self.server_info = info
        eprint(
            bold_err(
                f"Connected: {info.get('name', '?')} {info.get('version', '')}"
                if isinstance(info, dict)
                else "Connected: (unknown server)"
            )
        )
        server_version = result.get("protocolVersion", "")
        if server_version and server_version != self.PROTOCOL_VERSION:
            eprint(
                red(
                    f"Warning: server protocol version {server_version} "
                    f"differs from client {self.PROTOCOL_VERSION}"
                )
            )
        self.transport.notify(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        return resp

    def list_tools(self) -> list[dict[str, Any]]:
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
        _check_error(resp)
        result = resp.get("result", {})
        if isinstance(result, dict):
            tools = result.get("tools", [])
            if isinstance(tools, list):
                return tools
        return []

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Invoke a tool by name with the given arguments."""
        return self.transport.request(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )

    def reconnect(self) -> list[dict[str, Any]]:
        """Reconnect transport, re-initialize, and return fresh tools list."""
        self._id_seq = 0
        self.transport.reconnect()
        self.initialize()
        return self.list_tools()
