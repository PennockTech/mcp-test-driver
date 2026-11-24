# 0011: Tests drive scenario-configured fake servers

Status: Accepted (2026-11-12)

## Context

Most of the driver's bugs live at the boundary: framing, stream
handling, timing, what a server does in an odd order.  Mocked transports
test the mock.

## Decision

Tests run the real client stack against fake servers in `tests/fakes/`:
a subprocess for stdio, a threaded HTTP server, an authorization server,
an HTTP proxy.  One engine (`engine.py`) implements MCP behaviour for
both transports and is configured by a JSON-able scenario dict.  New
server behaviour is a new scenario key, documented in the fake's
docstring.

Real implementations are covered by two opt-in suites: integration tests
against the official SDK servers, and the official conformance suite's
client scenarios.

## Consequences

- No patching of `Transport` methods to fake a server.  `fakes/memory.py`
  is for unit tests that need no server at all.
- Tests start processes and sockets, so they are slower than mocks;
  accepted.
- Details: [../../tests/AGENTS.md](../../tests/AGENTS.md).
