# User guide

What the [README](../README.md) leaves out.  For the full list of options
run `mcp-test-driver --help`; for commands, `/help` and `/help <command>`
at the prompt.  This guide explains behaviour, not syntax.

## Calling things

A line that does not start with `/` is a tool call: the tool's name, then
`key=value` arguments (values may be JSON lists or objects), or one JSON
object.  Builtin commands start with `/`; a leading `/` is stripped from
tool names, so a server cannot shadow a builtin.

Lists the server does not advertise are still fetched on demand, with a
note.  Capability changes you make at the prompt reach a handshake-era
server after `/reconnect`.

## Protocol eras and revisions

MCP has two eras: the `initialize` handshake (2024-11-05 to 2025-11-25)
and the stateless protocol (2026-07-28 onwards), where every request
carries its protocol version and capabilities in `_meta`.

By default the driver acts as the spec's dual-era client.  It probes with
`server/discover`, stays stateless if the server understands, and
otherwise falls back to `initialize`.  The start-up report (`/info`)
shows what was tried and why.

- `--era legacy` skips the probe; `--era modern` refuses legacy servers.
- `--protocol-version REV` offers or requests a specific revision.
- The revision sets defaults only.  `/ping` and other methods a revision
  removed are still sent if you ask, with a note.  `/loglevel` sets the
  per-request log level in the stateless era; `/loglevel <level> --rpc`
  sends `logging/setLevel` regardless.

## Findings

While you work, the driver notes anything worth knowing.  New findings
print before each prompt; `/findings` lists them all.

- **violation**: breaks a requirement of the revision the server claims
  (malformed tool schemas, duplicate tool names, unmatched response IDs,
  JSON-RPC batches outside 2025-03-26, ...).
- **beyond-claimed**: a feature from a later revision, seen while the
  server claims an older one.  Useful to know, not wrong.
- **deprecated**: a feature deprecated at the claimed revision.
- **note**: everything else, such as your sending a method the revision
  lacks.

## Probes and checks

The negotiated revision says what a server *claims*; these find out what
it *does*.

- `/probe` (`--probe`) sends targeted requests, such as `server/discover`
  to a handshake-era server, `ping` to a stateless one, methods without
  their capability, a JSON-RPC batch, the HTTP GET stream.  `/matrix`
  lays the results out as claimed versus observed.
- `/probe versions` (`--probe-versions`) reconnects offering each known
  revision and reports what the server negotiates, which also reveals
  dual-era servers.
- `/check` (`--check`) runs conformance checks: list results, completion,
  caching fields, error codes for unknown methods, unknown tools, missing
  resources and malformed JSON, and over HTTP whether a bad
  `MCP-Protocol-Version`, a mismatched `Mcp-Method` and a foreign `Origin`
  are rejected.  Check IDs follow the official conformance suite's
  scenario names where they test the same thing.

Probes never call the server's own tools; the only tool call names a tool
that does not exist.

For CI, `--check --json` prints one JSON document (protocol, server,
checks, findings) and exits 1 on failures, 2 if the server is
unreachable.  `-c LINE` (repeatable) runs any REPL lines and exits 1 if
one failed.

## Client capabilities

All are off until you enable them, so the server sees only what you
chose.  `/client` shows what is being sent.

- **Roots** (`--roots[=PATH]`, `/roots`): advertises directories as
  relevant.  It is a hint; it grants no access.  Paths are made
  canonical, and added roots must be under the base path.
- **Elicitation** (`--elicitation`, `/elicitation on`): the driver asks
  you.  Form mode walks the requested schema field by field.  URL mode
  shows the URL and its host and asks for consent; it never opens a
  browser.  `!decline` or `!cancel` at any prompt ends with that action.
  `--elicitation-auto` accepts forms with the schema's defaults, for
  scripted runs.
- **Sampling** (`--sampling`, `/sampling on`): you play the model.  The
  server's messages, system prompt and offered tools are shown; type the
  reply, `!json {...}` for a complete result such as a `tool_use` block,
  or `!decline`.
- **Progress tokens** (`--progress`) and **OpenTelemetry**
  `traceparent` (`--otel`) are attached to requests on request.

In the stateless era the same handlers answer multi round-trip requests:
the driver fulfils an `input_required` result and retries the call.

## Extensions

Extensions are off by default, as the spec asks.  `--extension ID` or
`/extension add ID` declares one; the start-up report lists the server's.

- **Tasks**, both generations.  `/task <tool>` calls a tool as a task
  and follows it to the end, polling at the server's interval and
  answering any input it asks for.  A plain tool call answered with a
  task handle is followed the same way.  Ctrl-C detaches; `/tasks`
  lists, gets, waits for, updates and cancels.
- **MCP Apps**: `/app` lists tools with a UI resource; `/app save`
  writes the HTML to a file.  It is never rendered in the terminal; open
  it in a sandboxed browser profile.
- **Skills**: `/skills` lists them; `/skills read` reads a file and
  checks it against the digest from the listing.
- **Structured output**: a tool with an `outputSchema` that returns no
  `structuredContent` is flagged.  If the `jsonschema` package is
  installed alongside, the content is validated against the schema too.

## Raw protocol access

`/send` and `/notify` send any method with any params, framed for the
negotiated revision.  `/raw` sends text exactly as typed, valid or not.
`/meta` and `/header` set `_meta` fields and HTTP headers that stay on
every request.  `/last` shows the last response as raw JSON; `/trace`
toggles display of all traffic.

## HTTP transports

- **Streamable HTTP** (2025-03-26 onwards): JSON or SSE replies,
  sessions, the GET listening stream (`/listen`), and resumption of
  broken streams.
- **HTTP+SSE** (2024-11-05, deprecated): used automatically when
  `initialize` over Streamable HTTP gets a 400, 404 or 405, or forced
  with `--transport sse`.  The POST endpoint must be on the same origin
  as the SSE URL.

Redirects are never followed.

## Proxies and TLS trust

Every HTTP request the driver makes (MCP traffic, metadata and OAuth
fetches, server cards) follows the same proxy and certificate settings.

**Proxies** come from the usual environment variables, as curl reads
them: `https_proxy` for https targets, `http_proxy` for http, then
`all_proxy`; the lowercase name wins.  `no_proxy` lists exceptions: `*`,
host names (`example.com` also covers its subdomains), IP addresses,
CIDR blocks, each optionally with `:port`.  Host names are matched as
written, never resolved.

- `--proxy URL` overrides the variables (`no_proxy` still applies);
  `--proxy direct` ignores them.
- Loopback targets are never proxied.
- `http://` and `https://` proxies are supported, with credentials in
  the URL; they are never printed.  SOCKS is not supported, and a SOCKS
  setting is an error rather than a silent direct connection.
- The start-up report shows the route, and connection errors name the
  proxy.

**TLS trust**: `--ca-bundle PATH` (or `MCP_TEST_DRIVER_CA_BUNDLE`) adds
the CA certificates in a PEM file, or an OpenSSL hashed directory, to the
system trust store, for both the server and an `https://` proxy.  To
*replace* the system store, use OpenSSL's `SSL_CERT_FILE` /
`SSL_CERT_DIR`.  `--insecure` turns verification off, for testing only.

```sh
# Inspect traffic with mitmproxy:
mcp-test-driver --proxy http://127.0.0.1:8080 \
    --ca-bundle ~/.mitmproxy/mitmproxy-ca-cert.pem https://example.com/mcp

# A sandbox (AI agents, CI) whose egress proxy re-signs TLS:
export HTTPS_PROXY=http://127.0.0.1:3128
export MCP_TEST_DRIVER_CA_BUNDLE=/path/to/sandbox-ca.pem
mcp-test-driver https://example.com/mcp
```

A proxy that re-signs TLS sees everything, bearer tokens included; use
one you control.

## Authorization

**Static credentials:** `--bearer-env VAR` (the token stays out of argv)
or `--header 'Name: value'`.

**OAuth** (`--oauth`, or `/auth login`): when the server answers 401, the
driver follows the MCP authorization spec and retries with the new token.

1. *Discovery*: the `WWW-Authenticate` challenge, Protected Resource
   Metadata, then authorization-server metadata.  `/auth discover` shows
   this chain on its own, without logging in.
2. *Client registration*, in the spec's order: your `--client-id` (with
   `--client-secret-env` for a confidential client), else our published
   [Client ID Metadata Document](https://mcp-test-driver.pennock.tech/oauth/client.json),
   else Dynamic Client Registration.
3. *Login*: authorization code with PKCE and a redirect to `127.0.0.1`
   (port 33418 if free; `--callback-port`).  The URL is printed, and
   `--browser` opens it.  If your browser is on another machine, paste
   the final redirect URL at the prompt.
4. *Afterwards*: expired tokens are refreshed, and a 403
   `insufficient_scope` triggers step-up re-authorization.

Other flows: `--auth-flow client-credentials` (machine to machine) and
`--auth-flow device` (headless machines, when the server offers it).
`--token-cache PATH` keeps credentials between runs, mode 0600.  `/auth
status`, `/auth refresh` and `/auth logout` manage the session.

Anything unusual becomes a finding.  The driver refuses to continue only
where continuing would leak credentials: metadata naming a different
resource, or an issuer that does not match.

## Limits

See [roadmap.md](roadmap.md) for what is not implemented, and
[designs/security.md](designs/security.md) for how the driver protects
you from a hostile server.
