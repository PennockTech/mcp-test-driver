# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for client-side request handlers and registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_test_driver.handlers import (
    HandlerRegistry,
    PingHandler,
    RootsHandler,
)


class TestPingHandler:
    """Tests for the PingHandler."""

    def test_returns_empty_dict(self) -> None:
        handler = PingHandler()
        assert handler.handle({}) == {}

    def test_ignores_params(self) -> None:
        handler = PingHandler()
        assert handler.handle({"extra": "stuff"}) == {}


class TestRootsHandler:
    """Tests for the RootsHandler."""

    def test_initial_root_is_base(self, tmp_path: Path) -> None:
        handler = RootsHandler(tmp_path)
        roots = handler.handle({})["roots"]
        assert len(roots) == 1
        assert roots[0]["uri"] == tmp_path.as_uri()
        assert roots[0]["name"] == tmp_path.name

    def test_base_property(self, tmp_path: Path) -> None:
        handler = RootsHandler(tmp_path)
        assert handler.base == tmp_path.resolve()

    def test_add_root_under_base(self, tmp_path: Path) -> None:
        subdir = tmp_path / "sub"
        subdir.mkdir()
        handler = RootsHandler(tmp_path)
        handler.add_root(subdir)
        roots = handler.handle({})["roots"]
        assert len(roots) == 2
        assert any(r["name"] == "sub" for r in roots)

    def test_add_root_outside_base_raises(self, tmp_path: Path) -> None:
        handler = RootsHandler(tmp_path)
        with pytest.raises(ValueError):
            handler.add_root(tmp_path.parent)

    def test_add_root_nonexistent_raises(self, tmp_path: Path) -> None:
        handler = RootsHandler(tmp_path)
        with pytest.raises(OSError):
            handler.add_root(tmp_path / "nonexistent")

    def test_add_root_duplicate_ignored(self, tmp_path: Path) -> None:
        handler = RootsHandler(tmp_path)
        # Adding the base path again should be a no-op
        handler.add_root(tmp_path)
        roots = handler.handle({})["roots"]
        assert len(roots) == 1

    def test_remove_root(self, tmp_path: Path) -> None:
        subdir = tmp_path / "sub"
        subdir.mkdir()
        handler = RootsHandler(tmp_path)
        handler.add_root(subdir)
        assert len(handler.roots) == 2
        removed = handler.remove_root(subdir.as_uri())
        assert removed is True
        assert len(handler.roots) == 1

    def test_remove_nonexistent_root_returns_false(self, tmp_path: Path) -> None:
        handler = RootsHandler(tmp_path)
        assert handler.remove_root("file:///nonexistent") is False

    def test_roots_property_returns_copy(self, tmp_path: Path) -> None:
        handler = RootsHandler(tmp_path)
        roots1 = handler.roots
        roots2 = handler.roots
        assert roots1 == roots2
        assert roots1 is not roots2  # copy, not reference

    def test_symlink_traversal_blocked(self, tmp_path: Path) -> None:
        """Symlinks that escape the base directory are blocked."""
        # Create a symlink inside tmp_path that points outside
        outside = tmp_path.parent / "outside_target"
        outside.mkdir(exist_ok=True)
        try:
            link = tmp_path / "escape"
            link.symlink_to(outside)
            handler = RootsHandler(tmp_path)
            # resolve(strict=True) follows symlinks, so the resolved path
            # is outside tmp_path → relative_to() should raise.
            with pytest.raises(ValueError):
                handler.add_root(link)
        finally:
            outside.rmdir()

    def test_nonexistent_base_raises(self) -> None:
        with pytest.raises(OSError):
            RootsHandler(Path("/nonexistent/path/12345"))


class TestHandlerRegistry:
    """Tests for the HandlerRegistry."""

    def test_register_and_dispatch(self) -> None:
        registry = HandlerRegistry()
        registry.register("ping", PingHandler())
        assert registry.has("ping")
        result = registry.dispatch("ping", {})
        assert result == {}

    def test_dispatch_unregistered_raises(self) -> None:
        registry = HandlerRegistry()
        with pytest.raises(KeyError):
            registry.dispatch("unknown/method", {})

    def test_has_returns_false_for_unregistered(self) -> None:
        registry = HandlerRegistry()
        assert registry.has("foo") is False

    def test_unregister(self) -> None:
        registry = HandlerRegistry()
        registry.register("ping", PingHandler())
        assert registry.has("ping")
        registry.unregister("ping")
        assert not registry.has("ping")

    def test_unregister_nonexistent_is_noop(self) -> None:
        registry = HandlerRegistry()
        registry.unregister("nonexistent")  # should not raise

    def test_capabilities_empty(self) -> None:
        registry = HandlerRegistry()
        assert registry.capabilities() == {}

    def test_capabilities_with_ping_only(self) -> None:
        registry = HandlerRegistry()
        registry.register("ping", PingHandler())
        # Ping doesn't produce a capability entry
        assert registry.capabilities() == {}

    def test_capabilities_with_roots(self, tmp_path: Path) -> None:
        registry = HandlerRegistry()
        registry.register("roots/list", RootsHandler(tmp_path))
        caps = registry.capabilities()
        assert "roots" in caps
        assert caps["roots"]["listChanged"] is True

    def test_capabilities_roots_removed_after_unregister(self, tmp_path: Path) -> None:
        registry = HandlerRegistry()
        registry.register("roots/list", RootsHandler(tmp_path))
        assert "roots" in registry.capabilities()
        registry.unregister("roots/list")
        assert "roots" not in registry.capabilities()


class TestTransportServerRequestDispatch:
    """Tests for server-to-client request handling in StdioTransport."""

    def test_server_request_dispatched(self) -> None:
        """When the server sends a request (method+id), the transport
        dispatches it via the handler registry and sends a response."""
        import io
        import json
        from unittest.mock import MagicMock, patch

        from mcp_test_driver.transport import StdioTransport

        # Server sends: a server request (roots/list), then the actual response
        server_request = {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "roots/list",
            "params": {},
        }
        client_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": []},
        }
        response_lines = (
            json.dumps(server_request, separators=(",", ":")).encode()
            + b"\n"
            + json.dumps(client_response, separators=(",", ":")).encode()
            + b"\n"
        )
        mock_stdout = io.BytesIO(response_lines)
        mock_stdin = io.BytesIO()

        mock_proc = MagicMock()
        mock_proc.stdin = mock_stdin
        mock_proc.stdout = mock_stdout
        mock_proc.stderr = None
        mock_proc.wait = MagicMock()

        with patch(
            "mcp_test_driver.transport.subprocess.Popen", return_value=mock_proc
        ):
            transport = StdioTransport(["fake"])

        transport.trace = False

        # Set up a handler registry with a roots handler
        registry = HandlerRegistry()
        registry.register("roots/list", RootsHandler(Path.cwd()))
        transport.handler_registry = registry

        # Send a request — the transport should handle the server request
        # in the middle, then return the actual response.
        result = transport.request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )

        assert result is not None
        assert result["id"] == 1

        # Verify the transport sent a response to the server request
        # by checking what was written to stdin (after our request).
        stdin_data = mock_stdin.getvalue()
        # Should contain at least two JSON messages: our request + the roots response
        lines = [x for x in stdin_data.split(b"\n") if x.strip()]
        assert len(lines) >= 2
        roots_response = json.loads(lines[1])
        assert roots_response["id"] == 99
        assert "roots" in roots_response["result"]

    def test_unsupported_server_request_returns_error(self) -> None:
        """Server requests for unregistered methods get an error response."""
        import io
        import json
        from unittest.mock import MagicMock, patch

        from mcp_test_driver.transport import StdioTransport

        server_request = {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "unknown/method",
            "params": {},
        }
        client_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {},
        }
        response_lines = (
            json.dumps(server_request, separators=(",", ":")).encode()
            + b"\n"
            + json.dumps(client_response, separators=(",", ":")).encode()
            + b"\n"
        )
        mock_stdout = io.BytesIO(response_lines)
        mock_stdin = io.BytesIO()

        mock_proc = MagicMock()
        mock_proc.stdin = mock_stdin
        mock_proc.stdout = mock_stdout
        mock_proc.stderr = None
        mock_proc.wait = MagicMock()

        with patch(
            "mcp_test_driver.transport.subprocess.Popen", return_value=mock_proc
        ):
            transport = StdioTransport(["fake"])

        transport.trace = False
        transport.handler_registry = HandlerRegistry()  # empty registry

        result = transport.request(
            {"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}
        )
        assert result is not None

        # Check that an error response was sent for the unknown method
        stdin_data = mock_stdin.getvalue()
        lines = [x for x in stdin_data.split(b"\n") if x.strip()]
        assert len(lines) >= 2
        error_response = json.loads(lines[1])
        assert error_response["id"] == 42
        assert "error" in error_response
        assert error_response["error"]["code"] == -32601


class TestMcpSessionRoots:
    """Tests for roots enable/disable on McpSession."""

    def test_enable_roots(self, tmp_path: Path) -> None:
        from tests.test_protocol import FakeTransport

        from mcp_test_driver.protocol import McpSession

        transport = FakeTransport([])
        session = McpSession(transport)
        session.enable_roots(tmp_path)
        assert session.roots_handler is not None
        assert session.roots_handler.base == tmp_path.resolve()

    def test_disable_roots(self, tmp_path: Path) -> None:
        from tests.test_protocol import FakeTransport

        from mcp_test_driver.protocol import McpSession

        transport = FakeTransport([])
        session = McpSession(transport)
        session.enable_roots(tmp_path)
        assert session.roots_handler is not None
        session.disable_roots()
        assert session.roots_handler is None

    def test_capabilities_include_roots_when_enabled(self, tmp_path: Path) -> None:
        from tests.test_protocol import FakeTransport

        from mcp_test_driver.protocol import McpSession

        transport = FakeTransport([])
        session = McpSession(transport)
        session.enable_roots(tmp_path)
        caps = session.handler_registry.capabilities()
        assert "roots" in caps

    def test_initialize_sends_roots_capability(self, tmp_path: Path) -> None:
        from tests.test_protocol import FakeTransport, _server_info_response

        from mcp_test_driver.protocol import McpSession

        transport = FakeTransport([_server_info_response()])
        session = McpSession(transport)
        session.enable_roots(tmp_path)
        session.initialize()

        req = transport.sent[0]
        assert "roots" in req["params"]["capabilities"]
