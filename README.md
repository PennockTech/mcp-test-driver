# mcp-test-driver

Interactive REPL for testing any [MCP](https://modelcontextprotocol.io/)
server, over stdio or HTTP.

Point it at a command (stdio) or a URL (HTTP) and get a prompt with
tab-completion for tools, argument keys, and enum values.

## Usage

```sh
# Stdio transport — pass the server command and its arguments:
mcp-test-driver aifr mcp
mcp-test-driver character agent mcp
mcp-test-driver nats-lsp mcp-stdio

# HTTP transport — pass a URL:
mcp-test-driver https://unicode.mcp.pennock.tech/mcp
```

At the prompt, type a tool name and its arguments:

```
mcp> unicode_search query=snowman
mcp> unicode_lookup_char char=✓
```

## Built-in Commands

Built-in commands are dot-prefixed to avoid colliding with MCP tool names:

| Command | Alias | Description |
|---|---|---|
| `.help` | `.h` | Show help (or `.help <tool>` for tool help) |
| `.list` | `.l` | List available tools |
| `.describe <tool>` | `.d` | Show full schema for a tool |
| `.reconnect` | `.rc` | Reconnect to the server |
| `.cache-flush` | `.cf` | Clear cached tools, re-fetch from server |
| `.trace` | `.t` | Toggle JSON-RPC protocol tracing |
| `.quit` | `.q` | Exit |

Press **Tab** to complete commands, tool names, argument keys (`key=`), and
enum values.  Press **F1** or **Esc-H** for context-sensitive help.

## Installation

```sh
# Install as a tool (recommended for CLI use):
uv tool install .

# Or install into a project:
uv pip install .

# Or run directly from the source tree:
uv run mcp-test-driver <server-command...>
```

## Development

```sh
uv sync --group dev
uv run ruff check mcp_test_driver/
uv run ty check
```

## License

ISC — see [LICENSE](LICENSE).
