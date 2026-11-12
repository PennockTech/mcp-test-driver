# Security Model

This document describes the security measures implemented in mcp-test-driver
to protect against malicious MCP servers.

## Threat Model

mcp-test-driver connects to arbitrary MCP servers — both local (stdio) and
remote (HTTPS).  A malicious server may attempt:

1. **Memory exhaustion** via oversized responses
2. **SSRF** via HTTP redirects to internal services
3. **Terminal injection** via ANSI escape sequences in tool names/descriptions
4. **HTTP header injection** via crafted session IDs
5. **Client hang** via slow or incomplete responses
6. **Response confusion** via mismatched JSON-RPC IDs
7. **Builtin command shadowing** via tool names that match builtin commands
8. **Stderr injection** via ANSI escape sequences written to subprocess stderr

## Implemented Defenses

### Size Limits (transport/)

| Limit | Value | Applies to |
|-------|-------|------------|
| `MAX_LINE_BYTES` | 16 MiB | Stdio readline — oversized lines are drained and discarded |
| `MAX_RESPONSE_BYTES` | 16 MiB | HTTP JSON body read + SSE stream total bytes |

### Network Security (transport/http.py)

- **No HTTP redirects**: `urllib3.util.Retry(redirect=0)` prevents SSRF.
  3xx responses are explicitly rejected with an error message.
- **HTTP status validation**: 4xx and 5xx are rejected before body parsing.
- **Session expiry detection**: HTTP 404 with an active session ID raises
  `SessionExpiredError`, signaling the client to re-initialize.
- **Separate timeouts**: 10s connect, 60s read (via `urllib3.util.Timeout`).
- **TLS verification**: On by default.  `--insecure` disables it for
  testing with self-signed certs.
- **Header injection**: user-supplied headers (`--header`, `/header`) and
  every header we build are checked for CR, LF and NUL before sending.
- **Credentials**: `--bearer-env` reads the token from the environment so
  it never appears in argv or shell history; trace output never prints
  request headers, and `redact_headers()` masks `Authorization` and cookies
  wherever headers are shown.
- **TLS error messages**: SSLError distinguished from connection errors.
- **Session termination**: HTTP DELETE sent on close when a session ID is
  active, per MCP spec.

### Input Sanitization (sanitize.py, render.py, completion.py, protocol.py)

- **`sanitize()`**: Strips ANSI escape sequences, OSC sequences, control
  characters (0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F, 0x7F, 0x9B), and Unicode
  bidirectional overrides/isolates (U+202A-202E, U+2066-2069; "Trojan
  Source").  Applied to all server-supplied data before terminal display.
- **`safe_json()`**: JSON for display, sanitized after encoding (with
  `ensure_ascii=False`, `json.dumps` passes 0x9B and bidi characters through).
- **Result rendering** (`render.py`): every content block, error message,
  `_meta` and unknown field is printed through `sanitize()`/`safe_json()`.
  Binary content (images, audio, blobs) is summarised by type and size,
  never dumped.
- **Session ID**: Stripped to visible ASCII only (0x21-0x7E per MCP spec).
- **Tool names**: Sanitized, spaces replaced with underscores, leading `/`
  stripped (prevents builtin shadowing).  Empty names are skipped.  The
  server's real name is kept separately, so calls still use exactly the
  name the server declared.
- **Server name/version**: Sanitized in connection banner.
- **Enum values**: Sanitized in tool help display.
- **Subprocess stderr**: Captured via pipe and read in a daemon thread,
  which only queues lines; the main thread sanitizes and prints them, so
  they never interleave with the prompt mid-line.

### Builtin Command Protection (completion.py)

- Builtin commands use the `/` prefix (e.g., `/help`, `/list`, `/quit`).
- Tool names are stripped of leading `/` during sanitization, making it
  impossible for a malicious server to shadow builtins.
- A tool named `/list` becomes `list` in the REPL; the real `/list` is
  always the builtin.

### Protocol Validation (protocol.py)

- **JSON-RPC error detection**: `error_from()` turns error responses into
  `McpError`.
- **Response ID matching**: the session waits for the response whose ID
  matches its request.  A response with any other ID is reported (and
  recorded as a finding) and never handed to the wrong caller; late replies
  to requests we cancelled are discarded quietly.
- **IDs are never reused**: the sequence is monotonic across reconnects and
  skips any IDs the user chose in `/raw`.
- **Server notifications and requests** arriving while we wait are handled
  in order (displayed, or answered via the handler registry) with no limit
  on how many may precede the response; the read timeout bounds the wait.
- **Protocol version negotiation**: counter-offers, unknown versions and
  aliases are accepted best-effort and recorded as findings.
- **Response structure validation**: Missing/malformed fields handled gracefully.
- **Pagination**: `tools/list` follows `nextCursor` across pages, with a
  safety limit of 100 pages to prevent infinite loops from a rogue server.

### Stdio Transport (transport.py)

- **Read timeout**: reads use `select()` on the raw stdout descriptor and
  our own line buffer, so the timeout (default 120s, `--timeout`) holds even
  when a server writes part of a line and stalls.  On timeout the client
  sends `notifications/cancelled` for the request, as the spec advises.
- **Oversized lines**: a line over `MAX_LINE_BYTES` is discarded whether or
  not its newline has arrived yet, and reading resumes at the next line.
- **Stderr filtering**: Subprocess stderr is captured via a pipe and read in
  a daemon thread.  Lines are sanitized before display with a `[server stderr]`
  prefix.

### SSE Stream Validation (transport/sse.py)

- **Spec-conformant parsing**: events are parsed per the WHATWG rules
  (`data`, `event`, `id`, `retry`, comments), incrementally, so a stream
  that stays open is processed as events arrive.
- **Malformed JSON warnings**: When SSE `data:` payload fails to parse as
  JSON, a warning is emitted to stderr instead of silently ignoring.
- **Byte limit**: Total SSE stream bytes are capped at `MAX_RESPONSE_BYTES`.

### Fetched Documents (fetch.py, auth_diag.py, legacy SSE)

Some URLs come from the server: OAuth metadata locations, the legacy
HTTP+SSE `endpoint`, server cards.

- **Metadata fetches** (`JsonFetcher`): GET only, no redirects, a 1 MiB
  cap, HTTPS required except to loopback hosts, and no credentials sent.
  A server can make the client fetch a public HTTPS URL of its choosing;
  it cannot make it reach plain-HTTP internal services or follow a
  redirect.
- **Legacy SSE endpoint**: must be on the same origin (scheme, host, port)
  as the SSE URL; otherwise the connection is refused, since our POSTs
  (and any `Authorization` header) would follow it.
- **Reader threads**: each SSE stream is read by a daemon thread that only
  parses and queues; the main thread prints.  Any exception in a thread
  whose stream was abandoned is ignored; otherwise it is reported as a
  broken stream.

### OAuth (oauth.py)

- **Loopback redirect listener**: bound to 127.0.0.1 only, accepts a single
  request on `/callback`, and lives only for the login (5 minutes at most).
- **CSRF and mix-up**: `state` must match; a present `iss` must equal the
  issuer (RFC 9207), and a missing `iss` is refused when the AS advertises
  it.  PKCE (S256) is always used.
- **Audience**: protected-resource metadata whose `resource` is not this
  server is refused (RFC 9728 §3.3), and every authorization and token
  request carries the RFC 8707 `resource` indicator.
- **Secrets**: client secrets come from the environment
  (`--client-secret-env`), never argv.  Tokens are never traced; `/auth
  status` shows only the first characters.  The token cache is written
  with mode 0600 and ignored (with a warning) if group- or world-readable;
  entries are keyed by server and record the issuer, so a different
  authorization server means a new registration (2026-07-28).
- **Browser**: the authorization URL is printed (sanitized); a browser is
  opened only with `--browser`.

### Interactive Handlers (interactive.py)

Elicitation and sampling are opt-in (`--elicitation`, `--sampling`): with
neither, the client declares no such capability and answers those requests
with "method not supported".

- Everything a server shows the user (message, field titles, descriptions,
  enum labels, sampled conversation, URLs) is sanitized.
- URL-mode elicitation never opens a browser.  The URL's host is shown
  separately so a look-alike link is easier to spot.
- Form mode cannot be told to hide input; per the spec, servers must not
  ask for secrets in form mode, and the driver echoes what you type.
- Tab-completion is switched off while answering, so server-controlled
  names cannot be completed into an answer by accident.

### Raw Protocol Commands (commands/protocol_cmds.py)

`/send`, `/notify` and `/raw` deliberately send whatever the user types,
including invalid JSON-RPC, so that servers can be tested against bad
input.  Only the user can trigger them; nothing a server sends is ever
turned into a raw send.

### Argument Parsing (parse.py)

- **Type validation**: JSON input (starting with `{`) is validated to be a
  dict.  Arrays, strings, and other JSON types are rejected with a clear
  error message.

### Error Handling (cli.py, repl.py)

- **Subprocess errors**: FileNotFoundError, PermissionError caught with
  user-friendly messages.
- **REPL resilience**: TransportError, ConnectionError caught in main loop
  so the REPL survives transient failures.
- **Ctrl-C during a request** sends `notifications/cancelled` and returns
  to the prompt instead of exiting.
- **Session expired**: `SessionExpiredError` (HTTP 404) caught with a message
  directing the user to `/reconnect`.
- **Clean exit**: KeyboardInterrupt → exit 130.

## What Is NOT Implemented

- **OAuth extensions**: enterprise-managed authorization (ID-JAG), DPoP,
  `private_key_jwt` and WIF jwt-bearer are not implemented (discovery
  shows what an AS offers).
- **DNS discovery**: only unofficial third-party drafts exist.
- **Server Cards / AI Catalog**: fetched only on request (`/server-card`).
