# Stage 1 → Stage 2 Handoff

## Completed

Stage 1 is complete.  The tool connects to stdio MCP servers, lists tools,
invokes tools with arguments, and provides tab-completion for tool names,
argument keys, and enum values.

### What works

- `uv run mcp-test-driver aifr mcp` — connects, initializes, REPL runs
- Dot-commands: `.list`, `.help`, `.describe`, `.reconnect`, `.cache-flush`, `.trace`, `.quit`
- Aliases: `.h`, `.l`, `.d`, `.rc`, `.cf`, `.t`, `.q`
- Tab-completion: first-word (dot-commands + tools), arg keys (`key=`), enum values
- F1/Esc-H keybindings bound via readline macro (prepends `.help` to current line and submits)
- Protocol tracing toggle (`.trace`)
- Session cache cleared on `.reconnect` and `.cache-flush`
- `ruff check` passes clean

### Known issues / notes

- HTTP transport is stubbed (`cli.py:_run_http` prints "not yet implemented")
- The old `mcp_test_driver.py` at the repo root is still present (should be deleted after Stage 2 or when convenient)
- `pyproject.toml` has `module-root = ""` under `[tool.uv.build-backend]` for flat layout
- `ty check` not yet run (may surface type issues)

## Stage 2 scope

Implement `HttpTransport` in `transport.py`:

1. Use `urllib3>=2.0` (already a dependency)
2. POST JSON-RPC to the endpoint URL
3. Handle `Content-Type: application/json` responses directly
4. Handle `Content-Type: text/event-stream` (SSE) responses with a minimal parser
5. Track `Mcp-Session-Id` header across requests
6. `reconnect()` clears session ID
7. Update `cli.py:_run_http()` to use HttpTransport
8. Test with `https://unicode.mcp.pennock.tech/mcp`
