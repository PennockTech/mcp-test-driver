# Copyright 2026 Phil Pennock — see LICENSE file.

"""Client-side request handlers for MCP server-to-client requests.

MCP is bidirectional: the server can send JSON-RPC requests to the client,
not just the other way around.  This module provides a registry of handlers
that the transport layer dispatches to when it receives a server-initiated
request (a message with both "method" and "id" fields).

Currently supported server-to-client methods:

  - ping              → empty response (keepalive)
  - roots/list        → list of filesystem root URIs the server may access

Handlers are registered in a HandlerRegistry, which also derives the
client capabilities dict sent during initialize.  The transport holds a
reference to the registry and dispatches incoming server requests through it.

Security notes (roots):

  - Roots only *hint* to the server which directories are relevant.
    The server still uses its own filesystem access to read files.
  - All root paths are resolved to absolute canonical paths (symlinks
    followed) before being advertised.
  - add_root() enforces that new paths are descendants of the base path
    using Path.relative_to(), preventing traversal attacks.
  - The base path itself must exist (strict=True resolution).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class ClientHandler(Protocol):
    """Protocol for a handler that responds to a server-to-client request."""

    def handle(self, params: dict[str, Any]) -> dict[str, Any]:
        """Process a server request and return the result dict."""
        ...


class PingHandler:
    """Responds to server ping requests with an empty result."""

    def handle(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}


class RootsHandler:
    """Responds to roots/list with filesystem root URIs.

    The server uses these URIs to understand which directories it should
    scope its operations to.  This handler enforces that all advertised
    roots are under the configured base path, preventing a caller from
    accidentally exposing arbitrary filesystem locations.

    The base_path is resolved to an absolute canonical path on construction.
    Subsequent add_root() calls verify that the new path is a descendant
    of base_path using Path.relative_to().
    """

    def __init__(self, base_path: Path) -> None:
        # Resolve to absolute canonical path.  strict=True ensures it
        # exists and resolves all symlinks, so we compare real paths.
        self._base = base_path.resolve(strict=True)
        self._roots: list[dict[str, str]] = [
            {"uri": self._base.as_uri(), "name": self._base.name},
        ]

    @property
    def base(self) -> Path:
        return self._base

    @property
    def roots(self) -> list[dict[str, str]]:
        return list(self._roots)

    def handle(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"roots": self._roots}

    def add_root(self, path: Path) -> None:
        """Add a root URI if it is under the base path.

        Raises ValueError if the path is not a descendant of the base path
        or does not exist.
        """
        resolved = path.resolve(strict=True)
        # This raises ValueError if resolved is not under self._base.
        resolved.relative_to(self._base)
        uri = resolved.as_uri()
        if not any(r["uri"] == uri for r in self._roots):
            self._roots.append({"uri": uri, "name": resolved.name})

    def remove_root(self, uri: str) -> bool:
        """Remove a root by URI.  Returns True if found and removed."""
        for i, r in enumerate(self._roots):
            if r["uri"] == uri:
                self._roots.pop(i)
                return True
        return False


class HandlerRegistry:
    """Maps server-to-client method names to handlers.

    The transport layer holds a reference to this registry and calls
    dispatch() when it receives a server request.  The capabilities()
    method returns the dict to include in the initialize request,
    derived from which handlers are registered.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, ClientHandler] = {}

    def register(self, method: str, handler: ClientHandler) -> None:
        """Register a handler for a server-to-client method."""
        self._handlers[method] = handler

    def unregister(self, method: str) -> None:
        """Remove a handler.  No-op if not registered."""
        self._handlers.pop(method, None)

    def has(self, method: str) -> bool:
        """Check if a handler is registered for the given method."""
        return method in self._handlers

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a server request to the registered handler.

        Raises KeyError if no handler is registered for the method.
        """
        handler = self._handlers[method]
        return handler.handle(params)

    def capabilities(self) -> dict[str, Any]:
        """Build the client capabilities dict from registered handlers.

        This dict is sent in the initialize request so the server knows
        which server-to-client methods it can call.
        """
        caps: dict[str, Any] = {}
        if "roots/list" in self._handlers:
            # listChanged: True tells the server we will send
            # notifications/roots/list_changed if roots change.
            caps["roots"] = {"listChanged": True}
        return caps
