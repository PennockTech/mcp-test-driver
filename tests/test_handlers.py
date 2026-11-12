# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for client-side request handlers and registry."""

from __future__ import annotations

from pathlib import Path

from mcp_test_driver.protocol import McpSession
from tests.fakes.memory import MemoryTransport

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


class TestSessionServerRequestDispatch:
    """The session answers server-to-client requests via the registry."""

    def _session(self, server_msgs: list[dict]) -> tuple[McpSession, MemoryTransport]:
        def responder(payload: dict) -> list[dict]:
            if isinstance(payload, dict) and payload.get("method") == "tools/list":
                return [*server_msgs, {"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": []}}]
            return []

        t = MemoryTransport(responder)
        return McpSession(t), t

    def test_server_request_dispatched(self, tmp_path: Path) -> None:
        s, t = self._session([{"jsonrpc": "2.0", "id": 99, "method": "roots/list", "params": {}}])
        s.enable_roots(tmp_path)
        assert s.list_tools() == []
        reply = t.sent[1]
        assert reply["id"] == 99 and reply["result"]["roots"][0]["uri"] == tmp_path.resolve().as_uri()

    def test_unsupported_server_request_returns_error(self) -> None:
        s, t = self._session([{"jsonrpc": "2.0", "id": 42, "method": "unknown/method"}])
        s.list_tools()
        assert t.sent[1]["id"] == 42 and t.sent[1]["error"]["code"] == -32601

    def test_handler_exception_becomes_internal_error(self) -> None:
        class Boom:
            def handle(self, params: dict) -> dict:
                raise RuntimeError("kaboom")

        s, t = self._session([{"jsonrpc": "2.0", "id": 7, "method": "x/boom"}])
        s.handler_registry.register("x/boom", Boom())
        s.list_tools()
        assert t.sent[1]["error"] == {"code": -32603, "message": "kaboom"}


class TestMcpSessionRoots:
    """Tests for roots enable/disable on McpSession."""

    def test_enable_disable(self, tmp_path: Path) -> None:
        session = McpSession(MemoryTransport())
        session.enable_roots(tmp_path)
        assert session.roots_handler is not None
        assert session.roots_handler.base == tmp_path.resolve()
        assert session.client_capabilities() == {"roots": {"listChanged": True}}
        session.disable_roots()
        assert session.roots_handler is None
        assert session.client_capabilities() == {}

    def test_list_changed_only_after_initialize(self, tmp_path: Path) -> None:
        t = MemoryTransport()
        session = McpSession(t)
        session.enable_roots(tmp_path)
        assert session.roots_list_changed() is False
        session.state.initialized = True
        session.state.client_capabilities = session.client_capabilities()
        assert session.roots_list_changed() is True
        assert t.sent[-1]["method"] == "notifications/roots/list_changed"
