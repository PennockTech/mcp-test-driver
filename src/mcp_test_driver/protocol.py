# Copyright 2026 Phil Pennock — see LICENSE file.

"""MCP JSON-RPC protocol layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .color import bold_err, eprint, red
from .handlers import HandlerRegistry, PingHandler
from .transport import sanitize

if TYPE_CHECKING:
    from .transport import Transport

# Safety limit to prevent infinite pagination loops from a rogue server.
_MAX_PAGES = 100


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


def _check_id(resp: dict[str, Any], expected_id: int) -> None:
    """Warn if the response ID doesn't match the expected request ID."""
    resp_id = resp.get("id")
    if resp_id != expected_id:
        eprint(
            red(
                f"Warning: response id={resp_id} does not match "
                f"request id={expected_id}"
            )
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
        self.server_capabilities: dict[str, Any] = {}
        # Handler registry for server-to-client requests.
        # Ping is always registered; other handlers (roots, etc.) are
        # added via enable_roots() or directly on the registry.
        self.handler_registry = HandlerRegistry()
        self.handler_registry.register("ping", PingHandler())
        # Give the transport a reference so it can dispatch server requests.
        self.transport.handler_registry = self.handler_registry

    def _next_id(self) -> int:
        self._id_seq += 1
        return self._id_seq

    def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and return the validated response.

        Raises ConnectionError if the server closes the connection,
        and McpError if the server returns a JSON-RPC error.
        """
        req_id = self._next_id()
        resp = self.transport.request(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {},
            }
        )
        if resp is None:
            raise ConnectionError(f"Server closed connection during {method}")
        _check_id(resp, req_id)
        _check_error(resp)
        return resp

    def _paginated_list(
        self,
        method: str,
        result_key: str,
    ) -> list[dict[str, Any]]:
        """Fetch a paginated list, following nextCursor across pages."""
        all_items: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {}
            if cursor is not None:
                params["cursor"] = cursor
            resp = self._request(method, params)
            result = resp.get("result", {})
            if isinstance(result, dict):
                items = result.get(result_key, [])
                if isinstance(items, list):
                    all_items.extend(items)
                cursor = result.get("nextCursor")
            else:
                break
            if not cursor:
                break
        return all_items

    def initialize(self) -> dict[str, Any]:
        """Perform MCP initialize handshake.

        Client capabilities are derived from the handler registry —
        if a roots handler is registered, we advertise roots support, etc.
        """
        req_id = self._next_id()
        resp = self.transport.request(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": self.handler_registry.capabilities(),
                    "clientInfo": {
                        "name": self.CLIENT_NAME,
                        "version": self.CLIENT_VERSION,
                    },
                },
            }
        )
        if resp is None:
            raise ConnectionError("Server closed connection during initialize")
        _check_id(resp, req_id)
        _check_error(resp)
        result = resp.get("result")
        if not isinstance(result, dict):
            raise ConnectionError("Server returned invalid initialize response")
        info = result.get("serverInfo", {})
        if isinstance(info, dict):
            self.server_info = info
        caps = result.get("capabilities", {})
        if isinstance(caps, dict):
            self.server_capabilities = caps
        if isinstance(info, dict):
            sname = sanitize(str(info.get("name", "?")))
            sver = sanitize(str(info.get("version", "")))
            eprint(bold_err(f"Connected: {sname} {sver}"))
        else:
            eprint(bold_err("Connected: (unknown server)"))
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

    # ── Tools ──────────────────────────────────────────────────────────

    def list_tools(self) -> list[dict[str, Any]]:
        """Fetch the list of available tools, following pagination cursors."""
        return self._paginated_list("tools/list", "tools")

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Invoke a tool by name with the given arguments."""
        req_id = self._next_id()
        resp = self.transport.request(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        if resp is not None:
            _check_id(resp, req_id)
        return resp

    # ── Resources ──────────────────────────────────────────────────────

    def list_resources(self) -> list[dict[str, Any]]:
        """Fetch the list of available resources, following pagination."""
        return self._paginated_list("resources/list", "resources")

    def list_resource_templates(self) -> list[dict[str, Any]]:
        """Fetch the list of resource templates, following pagination."""
        return self._paginated_list("resources/templates/list", "resourceTemplates")

    def read_resource(self, uri: str) -> dict[str, Any] | None:
        """Read a resource by URI."""
        resp = self._request("resources/read", {"uri": uri})
        return resp

    def subscribe_resource(self, uri: str) -> None:
        """Subscribe to updates for a resource."""
        self._request("resources/subscribe", {"uri": uri})

    def unsubscribe_resource(self, uri: str) -> None:
        """Unsubscribe from updates for a resource."""
        self._request("resources/unsubscribe", {"uri": uri})

    # ── Prompts ────────────────────────────────────────────────────────

    def list_prompts(self) -> list[dict[str, Any]]:
        """Fetch the list of available prompts, following pagination."""
        return self._paginated_list("prompts/list", "prompts")

    def get_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Get a prompt by name, optionally with arguments."""
        params: dict[str, Any] = {"name": name}
        if arguments:
            params["arguments"] = arguments
        resp = self._request("prompts/get", params)
        return resp

    # ── Utility ────────────────────────────────────────────────────────

    def ping(self) -> dict[str, Any]:
        """Send a ping and return the response."""
        return self._request("ping")

    def set_log_level(self, level: str) -> dict[str, Any]:
        """Set the server's logging level."""
        return self._request("logging/setLevel", {"level": level})

    def complete(
        self,
        ref_type: str,
        ref_name: str,
        argument_name: str,
        argument_value: str,
    ) -> list[str]:
        """Request argument completions from the server.

        ref_type: "ref/prompt" or "ref/resource"
        ref_name: the prompt or resource template name
        """
        resp = self._request(
            "completion/complete",
            {
                "ref": {"type": ref_type, "name": ref_name},
                "argument": {
                    "name": argument_name,
                    "value": argument_value,
                },
            },
        )
        result = resp.get("result", {})
        if isinstance(result, dict):
            completion = result.get("completion", {})
            if isinstance(completion, dict):
                values = completion.get("values", [])
                if isinstance(values, list):
                    return [str(v) for v in values]
        return []

    # ── Client capabilities ────────────────────────────────────────────

    def enable_roots(self, base_path: Any) -> None:
        """Enable the roots capability, advertising base_path to the server.

        Takes effect on the next initialize (i.e., after /reconnect).
        base_path should be a pathlib.Path.
        """
        from .handlers import RootsHandler

        handler = RootsHandler(base_path)
        self.handler_registry.register("roots/list", handler)

    def disable_roots(self) -> None:
        """Disable the roots capability.

        Takes effect on the next initialize (i.e., after /reconnect).
        """
        self.handler_registry.unregister("roots/list")

    @property
    def roots_handler(self) -> Any:
        """Return the current RootsHandler, or None if roots are disabled."""
        from .handlers import RootsHandler

        registry = self.handler_registry
        if registry.has("roots/list"):
            handler = registry._handlers["roots/list"]
            if isinstance(handler, RootsHandler):
                return handler
        return None

    # ── Session lifecycle ──────────────────────────────────────────────

    def reconnect(self) -> list[dict[str, Any]]:
        """Reconnect transport, re-initialize, and return fresh tools list."""
        self._id_seq = 0
        self.transport.reconnect()
        self.initialize()
        return self.list_tools()
