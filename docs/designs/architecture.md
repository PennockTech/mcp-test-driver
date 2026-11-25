# Architecture

Each module's docstring states its own design; read it before editing the
module.  This file is the map and the parts that span modules.

## Module map

```
src/mcp_test_driver/
  cli.py         argparse entry point; non-interactive modes (--check, --exec, ...)
  repl.py        the interactive loop, SessionCache (lists), task following
  commands/      builtin /commands: registry in __init__, one module per area
  completion.py  readline tab-completion, help keybindings, ReadlineModule Protocol
  helptext.py    /help, per-command and per-tool help
  protocol.py    McpSession: receive loop, per-revision framing, MRTR, dispatch
  negotiate.py   era and revision selection; fallback to initialize, then HTTP+SSE
  versions.py    registry of spec revisions: methods, capabilities, features
  messages.py    JSON-RPC message shapes; classify() and batch expansion
  transport/     stdio, Streamable HTTP, HTTP+SSE, SSE parser, x-mcp-header
  handlers.py    registry of client-side handlers for server requests (roots/list, ...)
  interactive.py elicitation and sampling handlers (interactive and automatic)
  tasks.py       tasks: 2025-11-25 core and the 2026 extension
  checks.py      passive conformance checks -> findings
  probe.py       active probes and the /check suite
  diagnostics.py Finding, DiagnosticLog, print_findings
  report.py      the connection report (start-up, /info)
  render.py      rendering results and content blocks, sanitized
  sanitize.py    sanitize() / safe_json() for all server-supplied text
  fetch.py       narrow JSON fetcher for server-supplied URLs
  net.py         proxy selection and TLS trust; the only builder of HTTP clients
  auth_diag.py   OAuth discovery (PRM, AS metadata) and its checks
  oauth.py       OAuth client: PKCE, CIMD/DCR, refresh, step-up, token cache
  parse.py       key=value argument parsing for the REPL
  color.py       ANSI colour helpers
tests/           one test file per module; fakes/ and integration/ (see tests/AGENTS.md)
docs/specs/      vendored upstream schemas and conformance lists
scripts/         fetch-specs.sh, conformance-client.sh
site/            GitHub Pages: the OAuth client metadata document
```

## Message flow

1. A `Transport` (`transport/base.py` Protocol) sends and receives parsed
   JSON-RPC messages.  It does not interpret them and holds no handlers.
2. `McpSession` (`protocol.py`) owns the receive loop.  It sends a
   request, then reads until the response with that ID arrives.  On the
   way, `messages.classify()` sorts each message:
   - notification: listeners (display, cache invalidation, diagnostics);
   - server request: the `handlers.py` registry, whose answer is sent back;
   - stray response: reported as a finding, never handed to another caller.
3. `checks.py` inspects what passes through and records findings in the
   session's `DiagnosticLog`; `render.py` prints results.
4. Before each prompt, `Repl.before_prompt()` drains idle-time messages,
   refreshes lists marked stale by `list_changed`, and prints new findings.

Request IDs are monotonic across reconnects and skip IDs the user chose
in `/raw`.  IDs of requests abandoned by Ctrl-C or a timeout are kept, so
late replies are dropped quietly.

One handler serves both eras: handshake-era server requests and the
`inputRequests` of a 2026-07-28 `InputRequiredResult` (MRTR) go through
the same registry.  The registry also derives the client capabilities the
driver declares.

## Threads

Reader threads exist for stdio stderr and for each open SSE stream.  They
parse and queue, nothing else; the main thread takes items off the queue
and is the only one that prints, so output never lands inside the readline
prompt ([decision 0005](../decisions/0005-main-thread-prints.md)).

## Commands

`commands/__init__.py` holds one registry.  `@command(...)` registers a
handler with its name, alias, summary, usage, help group and the kind of
its first argument (`Arg`); dispatch, `/help` and tab-completion all read
that table.  A line that does not start with `/` is a tool call.  Tool
names are displayed through `display_tool_name()`, which strips the `/`
prefix, so a server cannot shadow a builtin; the server's real name is
kept for the call.

## Readline

`gnureadline` is preferred; stdlib `readline` may be libedit (macOS),
which binds keys differently and may lack `set_pre_input_hook`.
`completion.py` handles both: keep its branches and `getattr` guards (see
the `ReadlineModule` docstring).  Interactive prompts for elicitation and
sampling switch the completer off while they run.

## Outbound HTTP

MCP transports, metadata fetches and OAuth all build clients with
`net.HttpClient` from the session's `NetConfig`, so proxy and CA settings
apply everywhere ([decision 0006](../decisions/0006-one-http-client.md)).
