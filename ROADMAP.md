# mcp-test-driver Roadmap

## Stage 1: Project scaffold & stdio transport

Create all project files.  Implement `color.py`, `parse.py`, `StdioTransport`,
`McpSession`, basic REPL with dot-commands, tab-completion.

**Result**: functional parity with original script but generalized to any stdio
MCP server.

**Test**: `uv run mcp-test-driver aifr mcp`

## Stage 2: HTTP/SSE transport

Implement `HttpTransport` with `urllib3` and minimal SSE parser.  Add
`Mcp-Session-Id` header tracking.

**Test**: `uv run mcp-test-driver https://unicode.mcp.pennock.tech/mcp`

## Stage 3: Help keybindings & polish

Implement F1 and Esc-H context-sensitive help.  Audit type annotations.  Run
`ruff check` and `ty check` clean.  Final README polish.
