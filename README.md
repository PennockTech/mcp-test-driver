# mcp-test-driver

Interactive REPL and diagnostic driver for any
[MCP](https://modelcontextprotocol.io/) server, over stdio or HTTP.  No AI
wiring, just a tool for developers building and maintaining MCP servers.

Point it at a command (stdio) or a URL (HTTP) and get:

- a prompt with tab-completion for tools, argument keys and enum values;
- a start-up report of the negotiated protocol revision, server identity,
  capabilities, and conformance findings;
- raw protocol access (`/send`, `/notify`, `/raw`) for testing anything the
  server offers, including features beyond the revision it claims.

The guiding rule: the negotiated revision decides *defaults* and how
findings are labelled; it never hides or blocks a feature.

Supported MCP revisions: 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25,
2026-07-28.  See [docs/mcp-feature-matrix.md](docs/mcp-feature-matrix.md)
for what each one defines and what the driver supports, and
[docs/roadmap-multi-version.md](docs/roadmap-multi-version.md) for planned
work.

## Usage

```sh
# Stdio transport — pass the server command and its arguments:
mcp-test-driver aifr mcp
mcp-test-driver character agent mcp
mcp-test-driver nats-lsp mcp-stdio

# HTTP transport — pass a URL:
mcp-test-driver https://unicode.mcp.pennock.tech/mcp

# Offer an older protocol revision:
mcp-test-driver --protocol-version 2025-03-26 my-server

# Enable the roots capability:
mcp-test-driver --roots aifr mcp                       # the current directory
mcp-test-driver --roots=/path/to/project https://example.com/mcp

# Authenticated HTTP servers (token taken from the environment, not argv):
TOKEN=... mcp-test-driver --bearer-env TOKEN https://example.com/mcp
mcp-test-driver --header 'X-Api-Key: abc' https://example.com/mcp
```

Options go before the target; everything after the target is passed to the
server command unchanged.

At the prompt, type a tool name and its arguments:

```
mcp> unicode_search query=snowman
mcp> unicode_lookup_char char=✓
```

## CLI Options

| Option                   | Description                                                     |
| ------------------------ | --------------------------------------------------------------- |
| `-h`, `--help`           | Show help                                                       |
| `--version`              | Show the version                                                |
| `--trace` / `--no-trace` | Show (default) or hide JSON-RPC traffic at start-up             |
| `-q`, `--quiet`          | Skip the start-up connection report                             |
| `--protocol-version REV` | Revision to offer (default: latest handshake revision)          |
| `--timeout SECS`         | How long to wait for a reply (default 120)                      |
| `--roots[=PATH]`         | Advertise a filesystem root (bare `--roots`: current directory) |
| `--transport KIND`       | Force `stdio` or `http` instead of guessing from the target     |
| `--header 'NAME: VALUE'` | Extra HTTP header (repeatable)                                  |
| `--bearer-env VAR`       | Send `Authorization: Bearer $VAR`                               |
| `--insecure`             | Skip TLS certificate verification (testing only)                |

## Built-in Commands

Built-in commands use the `/` prefix.  Tool names are stripped of leading
`/` during sanitization, so a server cannot shadow a builtin.  `/help`
lists everything; `/help <command>` shows usage.

### Tools, resources, prompts

| Command                         | Alias    | Description                                   |
| ------------------------------- | -------- | --------------------------------------------- |
| `/list`                         | `/l`     | List tools, with titles and behaviour hints   |
| `/describe <tool>`              | `/d`     | A tool's full definition, incl. output schema |
| `/resources`                    | `/lr`    | List resources                                |
| `/templates`                    | `/lt`    | List resource templates                       |
| `/read <uri>`                   | `/r`     | Read a resource                               |
| `/subscribe <uri>`              | `/sub`   | Subscribe to resource updates                 |
| `/unsubscribe <uri>`            | `/unsub` | Unsubscribe from resource updates             |
| `/prompts`                      | `/lp`    | List prompts                                  |
| `/prompt <name> [key=val ...]`  | `/p`     | Get a prompt                                  |

Lists the server does not advertise are fetched on demand, with a note.

### Server

| Command                                          | Alias | Description                                     |
| ------------------------------------------------ | ----- | ----------------------------------------------- |
| `/complete prompt\|resource <ref> <arg>=<partial>` | `/c`  | Argument completion (`completion/complete`)     |
| `/ping`                                          |       | Ping the server                                 |
| `/loglevel <level> [--rpc]`                      | `/ll` | Set the server log level                        |
| `/roots [on [path] \| off \| add <path> \| remove <uri>]` |  | Show or change the roots capability   |

### Protocol (raw access)

| Command                   | Description                                                     |
| ------------------------- | --------------------------------------------------------------- |
| `/send <method> [params]` | Send any request; framing follows the negotiated revision       |
| `/notify <method> [params]` | Send any notification                                         |
| `/raw <text>`             | Send JSON (or deliberately invalid text) exactly as typed       |
| `/meta key=val ...`       | Sticky `_meta` fields added to every request (`/meta clear`)    |
| `/header Name: value`     | Sticky HTTP headers (`/header clear`)                           |

### Diagnostics and session

| Command           | Alias | Description                                              |
| ----------------- | ----- | -------------------------------------------------------- |
| `/info`           | `/i`  | Connection report: protocol, server, capabilities, findings |
| `/findings`       | `/f`  | Conformance findings so far (`/findings clear`)          |
| `/last`           |       | The last response as raw JSON                            |
| `/reconnect`      | `/rc` | Reconnect and re-negotiate                               |
| `/cache-flush`    | `/cf` | Re-fetch tools, resources and prompts                    |
| `/trace [on\|off]` | `/t`  | Toggle JSON-RPC protocol tracing                         |
| `/help`           | `/h`  | Help (`/help <tool>` or `/help <command>`)               |
| `/quit`           | `/q`  | Exit                                                     |

Press **Tab** to complete commands, tool names, argument keys (`key=`),
enum values, prompt and resource names, and method names for `/send`.
Press **F1** or **Esc-h** for context-sensitive help at any point in a line.

## Conformance findings

While you work, the driver notes anything worth knowing, labelled by kind:

- **violation**: breaks a requirement of the revision the server claims
  (malformed tool schemas, duplicate tool names, unmatched response IDs,
  JSON-RPC batches after 2025-03-26, …);
- **beyond-claimed**: a feature from a later revision, observed while the
  server claims an older one (useful to know, not wrong);
- **deprecated**: a feature deprecated at the claimed revision;
- **note**: everything else, such as sending a method the revision lacks.

New findings are printed before each prompt; `/findings` lists them all.

## Client Capabilities

### Roots

The `--roots` flag (or `/roots on`) advertises the roots capability,
telling the server which filesystem directories are relevant.  The client
answers `roots/list` with the configured base path, and `/roots add` /
`/roots remove` send `notifications/roots/list_changed`.  This only hints
to the server where to look; it grants no access.

Security constraints:

- All root paths are resolved to absolute canonical paths (symlinks followed)
- Added roots must be descendants of the base path
- The base path must exist when roots are enabled

## Known Limitations

Work in progress is tracked in
[docs/roadmap-multi-version.md](docs/roadmap-multi-version.md).

- **2026-07-28 stateless protocol**: not yet negotiated automatically.
- **Elicitation and sampling** requests from the server are answered with
  "method not supported".
- **Legacy HTTP+SSE transport** (2024-11-05): not yet supported.
- **OAuth**: only static credentials (`--bearer-env`, `--header`).

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
uv run ruff check
uv run ty check
uv run pytest
```

The tests drive real subprocess and HTTP fake servers (`tests/fakes/`)
built from scenarios, so behaviour is checked end to end.  The protocol
revision registry is cross-checked against the vendored upstream schemas
in `docs/specs/`; refresh them with `scripts/fetch-specs.sh`.

## AI Disclosure

A LLM development tool was used extensively in the creation of this tool.

(It's a tool for exploring services designed for LLMs to use, what did you
expect?)

## License

ISC — see [LICENSE](LICENSE).
