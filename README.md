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
2026-07-28.  See [docs/mcp-feature-matrix.md](https://github.com/PennockTech/mcp-test-driver/blob/main/docs/mcp-feature-matrix.md)
for what each one defines and what the driver supports, and
[docs/roadmap-multi-version.md](https://github.com/PennockTech/mcp-test-driver/blob/main/docs/roadmap-multi-version.md) for planned
work.

## Installation

mcp-test-driver is on [PyPI](https://pypi.org/project/mcp-test-driver/) and
needs Python 3.12 or later.

```sh
# Run it without installing anything:
uvx mcp-test-driver <server-command...>

# Or install the command:
uv tool install mcp-test-driver
pipx install mcp-test-driver

# Or add it to a project's dev dependencies, then `uv run mcp-test-driver`:
uv add --dev mcp-test-driver
```

From a source checkout, `uv run mcp-test-driver <server-command...>` runs
the working tree; see [Development](#development).

## Usage

The examples assume the command is installed; otherwise put `uvx` in
front of each.

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
| `--era auto\|legacy\|modern` | Protocol era (default `auto`; see below)                     |
| `--protocol-version REV` | Revision to offer or request (default: the latest of each era)  |
| `--probe-timeout SECS`   | How long `--era auto` waits for `server/discover` (default 5)   |
| `--timeout SECS`         | How long to wait for a reply (default 120)                      |
| `--roots[=PATH]`         | Advertise a filesystem root (bare `--roots`: current directory) |
| `--elicitation[=MODES]`  | Answer elicitation requests interactively (`form`, `url`)       |
| `--elicitation-auto`     | Accept form elicitations automatically with schema defaults     |
| `--sampling`             | Answer sampling requests yourself (you play the LLM)            |
| `--progress`             | Attach progress tokens to calls                                 |
| `--otel`                 | Send a fresh OpenTelemetry `traceparent` with each request      |
| `--extension ID[=JSON]`  | Declare a client extension (repeatable)                         |
| `--check`                | Run the conformance checks, then exit (1 on failure, 2 if unreachable) |
| `--probe`                | Probe which features the server supports, then exit             |
| `--probe-versions`       | Offer each known revision in turn; report what is negotiated   |
| `--json`                 | With `--check`/`--probe`: one JSON document on stdout          |
| `-c LINE`, `--exec LINE` | Run REPL lines non-interactively, then exit (1 if any failed)  |
| `--conformance-client`   | Act as a client for the official conformance runner            |
| `--oauth`                | Log in with OAuth when the server asks (see Authorization)     |
| `--auth-flow FLOW`       | `code` (default), `client-credentials` or `device`             |
| `--client-id`, `--client-secret-env`, `--client-metadata-url` | OAuth client identity |
| `--callback-port`, `--browser`, `--scope`, `--token-cache` | OAuth login options     |
| `--transport KIND`       | Force `stdio`, `http` (Streamable HTTP) or `sse` (2024-11-05 HTTP+SSE) |
| `--no-listen`            | Don't open the HTTP GET stream after `initialize`               |
| `--header 'NAME: VALUE'` | Extra HTTP header (repeatable)                                  |
| `--bearer-env VAR`       | Send `Authorization: Bearer $VAR`                               |
| `--insecure`             | Skip TLS certificate verification (testing only)                |

## Protocol eras

MCP has two eras: the `initialize` handshake (2024-11-05 to 2025-11-25)
and the stateless protocol (2026-07-28 onwards), where every request
carries its protocol version and capabilities in `_meta`.  By default the
driver behaves as the spec's *dual-era client*: it probes with
`server/discover`, stays stateless if the server understands it (or
answers with a recognised modern error), and otherwise falls back to
`initialize`.  The start-up report shows what was tried and why.

- `--era legacy` skips the probe; `--era modern` refuses legacy servers.
- In the stateless era, the driver sends the per-request `_meta`, the
  `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name` and `Mcp-Param-*`
  (from `x-mcp-header`) HTTP headers, and answers multi round-trip
  `InputRequiredResult`s with the elicitation, sampling and roots handlers,
  retrying the request with `inputResponses` and `requestState`.
- `/loglevel` sets the per-request `_meta` log level; `/ping` and other
  removed methods are still sent if you ask, with a note.

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
| `/task <tool> [key=val ...]`    |          | Call a tool as a task, following it to the end |
| `/tasks list\|get\|wait\|result\|update\|cancel` | | Manage tasks                         |
| `/app [save <tool\|uri> <file>]` |         | MCP Apps: list UI resources, save one as HTML |
| `/skills [get\|read <uri>]`     |          | Skills extension: list, describe, read (digest-checked) |

Lists the server does not advertise are fetched on demand, with a note.

### Server

| Command                                          | Alias | Description                                     |
| ------------------------------------------------ | ----- | ----------------------------------------------- |
| `/complete prompt\|resource <ref> <arg>=<partial>` | `/c`  | Argument completion (`completion/complete`)     |
| `/ping`                                          |       | Ping the server                                 |
| `/discover`                                      |       | Send `server/discover` (in any era)             |
| `/listen [secs] [tools] [prompts] [resources] [uri ...]` | | Listen for unsolicited messages: `subscriptions/listen` (2026-07-28) or the HTTP GET stream |
| `/loglevel <level> [--rpc]`                      | `/ll` | Set the server log level                        |
| `/roots [on [path] \| off \| add <path> \| remove <uri>]` |  | Show or change the roots capability   |

### Client capabilities

| Command                              | Description                                         |
| ------------------------------------ | --------------------------------------------------- |
| `/client`                            | Capabilities and per-request options we send        |
| `/elicitation on [form,url] \| off`  | Answer elicitation requests interactively           |
| `/sampling on \| off`                | Answer sampling requests yourself                   |
| `/progress on \| off`                | Attach progress tokens to calls                     |
| `/otel on \| off`                    | Send (and print) a `traceparent` with each request  |
| `/extension [add ID [settings] \| remove ID]` | Declare client extensions; show both sides' |

Capability changes reach handshake-era servers after `/reconnect`.

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
| `/server-card [url]` |     | Fetch the Server Card and AI Catalog; compare with the live server |
| `/probe [versions]` |       | Probe what the server supports, or sweep protocol revisions |
| `/matrix`         |       | Claimed vs observed support for the probed features      |
| `/check`          |       | Conformance checks (error codes, headers, lists, caching) |
| `/auth [discover\|login\|status\|refresh\|logout]` | | OAuth discovery, login and token management |
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

## Extensions

Extensions (2026-07-28's `capabilities.extensions`) are off by default, as
the spec asks; `--extension ID` or `/extension add ID` declares one, and
the start-up report lists the server's.

- **Tasks**: both generations.  2025-11-25 core tasks: `/task <tool>`
  sends `params.task`, the driver polls `tasks/get` at the server's
  interval and fetches `tasks/result`.  The 2026 tasks extension
  (`io.modelcontextprotocol/tasks`): `/task` declares it, and any tool
  call answered with a task handle (`resultType: "task"`) is followed,
  answering `input_required` with `tasks/update` through the usual
  elicitation/sampling handlers.  Ctrl-C detaches; `/tasks` lists, gets,
  waits for, updates and cancels tasks.
- **MCP Apps** (`io.modelcontextprotocol/ui`): `/app` lists tools whose
  `_meta.ui.resourceUri` names a `ui://` resource; `/app save` writes the
  HTML to a file.  It is never rendered in the terminal.
- **Skills** (`io.modelcontextprotocol/skills`): `/skills` lists them,
  `/skills get` shows an entry, and `/skills read` reads a file and checks
  it against the digest from the listing.
- **Structured output**: a tool with an `outputSchema` that returns no
  `structuredContent` is flagged; with the `jsonschema` package installed
  alongside, the content is validated against the schema too.

## Probing and conformance checks

The negotiated revision says what a server *claims*; probes find out what
it *does*:

- `/probe` (`--probe`) sends targeted requests: `server/discover` on a
  handshake-era server, `ping` on a stateless one, `logging/setLevel`,
  completion, tasks and subscriptions without the capability, a JSON-RPC
  batch, the HTTP GET stream.  `/matrix` lays the results out as claimed
  vs observed; anything that works beyond the claimed revision, or fails
  within it, becomes a finding.
- `/probe versions` (`--probe-versions`) reconnects offering each known
  revision and reports what the server negotiates, which also shows
  dual-era servers.
- `/check` (`--check`) runs conformance checks: list results, completion,
  caching fields, error codes for unknown methods, unknown tools, missing
  resources and malformed JSON, and over HTTP whether bad
  `MCP-Protocol-Version`, mismatched `Mcp-Method` headers and foreign
  `Origin`s are rejected.  Check IDs follow the official conformance
  suite's scenario names where they test the same thing.
- `--check --json` prints one JSON document (protocol, server, checks,
  findings) and exits 1 on failures, 2 if the server is unreachable, so it
  fits in CI.  `-c '/probe' -c 'my_tool x=1'` scripts any REPL lines.

Probes never call the server's own tools; the only tool call names a tool
that does not exist.

The driver itself is tested as a client by the official
[conformance suite](https://github.com/modelcontextprotocol/conformance):
`scripts/conformance-client.sh` (or `task conformance`) runs every client
scenario in the 2025-11-25 and 2026-07-28 requirement lists, and CI runs it
on pull requests.

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

### Elicitation

With `--elicitation` (or `/elicitation on`), the driver declares the
elicitation capability and answers `elicitation/create` by asking you:

- `--elicitation-auto` accepts every form elicitation with the schema's
  defaults instead of asking (for scripted runs).
- **Form mode** walks the requested schema field by field, with types,
  defaults, ranges, and every enum style (plain, titled, multi-select).
- **URL mode** (2025-11-25+) shows the URL and its host prominently and
  asks for consent.  It never opens a browser; open the URL yourself if
  you trust it.
- Type `!decline` or `!cancel` at any prompt to end with that action.

A `-32042` "URL elicitation required" error is shown as the list of URLs
the server wants you to visit before retrying.

### Sampling

With `--sampling` (or `/sampling on`), the driver declares the sampling
capability and you answer `sampling/createMessage` yourself: the server's
messages, system prompt and offered tools are shown, and you type the
assistant's reply, `!json {...}` for a complete result (e.g. a `tool_use`
block), or `!decline`.  Handy for exercising agentic servers without
wiring up a model.

## HTTP transports

- **Streamable HTTP** (2025-03-26+): JSON or SSE replies, every SSE event
  routed (notifications and server requests mid-call included), sessions,
  the GET listening stream (`/listen`), and `Last-Event-ID` resumption
  honouring the server's `retry`.
- **HTTP+SSE** (2024-11-05, deprecated): used automatically when
  `initialize` over Streamable HTTP gets a 400/404/405, as the spec's
  backwards-compatibility section describes, or forced with
  `--transport sse`.  The POST endpoint must be on the same origin as the
  SSE URL.

## Authorization

**Static credentials:** `--bearer-env VAR` (the token stays out of argv)
or `--header`.

**OAuth login** (`--oauth`, or `/auth login` in the REPL): when the server
answers 401, the driver follows the MCP authorization spec end to end and
retries the request with the new token:

1. Discovery: the `WWW-Authenticate` challenge, Protected Resource Metadata
   (RFC 9728, from the header or the well-known URIs), and
   authorization-server metadata (RFC 8414 / OpenID Connect, in the spec's
   order).  `/auth discover` shows this chain on its own.
2. Client registration, in the spec's priority order: a pre-registered
   `--client-id` (with `--client-secret-env` for confidential clients,
   `client_secret_basic` or `client_secret_post` as the AS supports), our
   published [Client ID Metadata Document](https://mcp-test-driver.pennock.tech/oauth/client.json), or
   Dynamic Client Registration.
3. Authorization code with PKCE (S256) and a loopback redirect on
   `127.0.0.1` (port 33418 if free, else any; `--callback-port`).  The
   authorization URL is printed (`--browser` opens it); if your browser is
   on another machine, paste the final redirect URL at the prompt instead.
   Resource indicators (RFC 8707) go on every request, `iss` is validated
   (RFC 9207), and scope follows the spec: the challenge's, else the
   server's `scopes_supported`, plus `offline_access` when offered.
4. Afterwards: expired tokens are refreshed, and a 403 with
   `insufficient_scope` triggers step-up re-authorization (with a retry
   limit).

Other flows: `--auth-flow client-credentials` (machine-to-machine, needs
`--client-id` and `--client-secret-env`) and `--auth-flow device` (RFC 8628,
for headless machines, when the AS offers it).  `--token-cache PATH` keeps
credentials between runs (mode 0600, keyed by server and issuer).
`/auth status`, `/auth refresh` and `/auth logout` manage the session.

Everything unusual becomes a finding.  The driver refuses to continue only
where continuing would leak credentials: protected-resource metadata
naming a different resource, or an `iss` that doesn't match the issuer.
Metadata fetches are GET-only, never follow redirects, and require HTTPS
except to loopback hosts.

## Known Limitations

- OAuth extensions beyond client credentials (enterprise-managed
  authorization, DPoP, `private_key_jwt`, WIF) are not implemented;
  discovery shows what an authorization server offers.

## Development

```sh
uv sync --group dev
uv run ruff check
uv run ty check
uv run pytest
```

The tests drive real subprocess and HTTP fake servers (`tests/fakes/`)
built from scenarios, so behaviour is checked end to end.  Two opt-in
suites check against real implementations:

- `task test-integration` (or `MCPTD_INTEGRATION=1 uv run pytest -m
  integration`) runs the driver against the official Python SDK and the
  TypeScript "everything" reference server; needs network, uv and Node.
- `task conformance` runs the official conformance suite's client
  scenarios against the driver.

The protocol revision registry is cross-checked against the vendored
upstream schemas in `docs/specs/`; refresh them with
`scripts/fetch-specs.sh`.

## AI Disclosure

A LLM development tool was used extensively in the creation of this tool.

(It's a tool for exploring services designed for LLMs to use, what did you
expect?)

## License

ISC — see [LICENSE](https://github.com/PennockTech/mcp-test-driver/blob/main/LICENSE).
