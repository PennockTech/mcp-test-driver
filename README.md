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

# Enable roots capability (exposes cwd to the server):
mcp-test-driver --roots aifr mcp
mcp-test-driver --roots=/path/to/project https://example.com/mcp
```

At the prompt, type a tool name and its arguments:

```
mcp> unicode_search query=snowman
mcp> unicode_lookup_char char=✓
```

## CLI Options

| Option | Description |
|---|---|
| `-h`, `--help` | Show help message |
| `--trace` | Enable protocol tracing (default) |
| `--no-trace` | Disable protocol tracing at startup |
| `--roots` | Advertise cwd as filesystem root to the server |
| `--roots=<path>` | Advertise a specific path as filesystem root |

## Built-in Commands

Built-in commands use the `/` prefix.  Tool names are stripped of leading
`/` during sanitization, so a malicious server cannot shadow builtins.

### Tools

| Command | Alias | Description |
|---|---|---|
| `/list` | `/l` | List available tools |
| `/describe <tool>` | `/d` | Show full schema for a tool |

### Resources

| Command | Alias | Description |
|---|---|---|
| `/resources` | `/lr` | List available resources |
| `/templates` | `/lt` | List resource templates |
| `/read <uri>` | `/r` | Read a resource by URI |
| `/subscribe <uri>` | `/sub` | Subscribe to resource updates |
| `/unsubscribe <uri>` | `/unsub` | Unsubscribe from resource updates |

### Prompts

| Command | Alias | Description |
|---|---|---|
| `/prompts` | `/lp` | List available prompts |
| `/prompt <name> [args]` | `/p` | Get a prompt (with optional key=val arguments) |

### Session & Diagnostics

| Command | Alias | Description |
|---|---|---|
| `/ping` | | Ping the server |
| `/loglevel <level>` | `/ll` | Set server log level |
| `/roots` | | Show roots status |
| `/roots on [path]` | | Enable roots capability (takes effect on `/reconnect`) |
| `/roots off` | | Disable roots capability |
| `/reconnect` | `/rc` | Reconnect to the server |
| `/cache-flush` | `/cf` | Clear cached tools/resources/prompts, re-fetch |
| `/trace` | `/t` | Toggle JSON-RPC protocol tracing |
| `/help` | `/h` | Show help (or `/help <tool>` for tool help) |
| `/quit` | `/q` | Exit |

Press **Tab** to complete commands, tool names, argument keys (`key=`), and
enum values.  Press **F1** or **Esc-H** for context-sensitive help.

## Client Capabilities

### Roots

The `--roots` flag (or `/roots on` command) advertises the
[roots capability](https://modelcontextprotocol.io/specification/2025-03-26/client/roots)
to the server, telling it which filesystem directories are relevant.

When enabled, the client responds to `roots/list` requests from the server
with the configured base path.  This is read-only: it hints to the server
where to look, but the server accesses files using its own mechanisms.

Security constraints:

- All root paths are resolved to absolute canonical paths (symlinks followed)
- Additional roots added via `RootsHandler.add_root()` must be descendants
  of the base path (`Path.relative_to()` check prevents traversal)
- The base path must exist at the time roots are enabled

### Sampling

The MCP `sampling` capability (server asks the client to generate LLM
completions) is **not implemented**.  This is out of scope for a test driver.

## Protocol Coverage

mcp-test-driver implements the following MCP protocol methods:

| Method | Direction | Notes |
|---|---|---|
| `initialize` | client → server | Advertises capabilities from handler registry |
| `notifications/initialized` | client → server | |
| `tools/list` | client → server | Paginated |
| `tools/call` | client → server | |
| `resources/list` | client → server | Paginated; fetched if server advertises resources |
| `resources/templates/list` | client → server | Paginated |
| `resources/read` | client → server | |
| `resources/subscribe` | client → server | |
| `resources/unsubscribe` | client → server | |
| `prompts/list` | client → server | Paginated; fetched if server advertises prompts |
| `prompts/get` | client → server | |
| `ping` | both directions | Client handles server-initiated pings |
| `logging/setLevel` | client → server | |
| `completion/complete` | client → server | |
| `roots/list` | server → client | When `--roots` is enabled |

## Known Limitations

- **HTTP server-to-client requests**: Server-initiated requests (like
  `roots/list`) over the HTTP Streamable transport require a long-lived GET
  SSE stream, which is not yet implemented.  Server requests are fully
  supported on the stdio transport.  The `--roots` flag still works over
  HTTP for the initial handshake capabilities, but the server cannot
  dynamically query roots mid-session.

- **Partial-line stdio timeout**: The `select()`-based read timeout (120s)
  catches complete server silence, but if a server sends bytes without a
  newline, `readline()` may still block.

- **No OAuth/authentication**: The MCP spec defines OAuth 2.1 flows, but
  as a test driver we don't implement auth.  Use `Authorization` headers
  or environment-based credentials if needed.

- **No DNS discovery**: SRV/TXT/DANE-based MCP server discovery
  (SEP-1959) is proposed but not standardized.

- **No `.well-known` metadata**: Neither OAuth resource metadata (RFC 9728)
  nor MCP server cards (SEP-1649/1960) are fetched.

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
uv run ruff check src/
uv run ty check
uv run pytest tests/
```

## License

ISC — see [LICENSE](LICENSE).
