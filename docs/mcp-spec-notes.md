# MCP Specification Notes

Working notes on the parts of the MCP spec that matter most to a test
driver: transports, sessions, discovery and authorization.

These notes cover every released revision, from 2024-11-05 to
**2026-07-28** (the latest).  For the feature-by-revision breakdown, see
[mcp-feature-matrix.md](mcp-feature-matrix.md).  The authoritative schemas
are vendored under [specs/](specs/).

## Eras

- **Handshake era** (2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25):
  - The client sends `initialize` with its preferred version.
  - The server echoes that version, or counters with one it supports.
  - The client sends `notifications/initialized`.
  - The negotiated version and capabilities then hold for the connection
    (stdio) or the `Mcp-Session-Id` session (HTTP).
- **Modern / stateless era** (2026-07-28 onwards; SEP-2575, SEP-2567):
  - There is no handshake and there are no sessions.
  - Every request carries these keys in `params._meta`:
    - `io.modelcontextprotocol/protocolVersion` (required)
    - `io.modelcontextprotocol/clientCapabilities` (required)
    - `io.modelcontextprotocol/clientInfo` (optional)
    - `io.modelcontextprotocol/logLevel` (optional)
  - A request missing a required key gets `-32602`, plus HTTP 400 over HTTP.
  - Servers SHOULD return `io.modelcontextprotocol/serverInfo` in each result's `_meta`.
  - `server/discover` (MUST implement) returns `supportedVersions`, `capabilities` and `instructions`.
  - A version the server doesn't support gets `UnsupportedProtocolVersion` (`-32022`), whose data lists the supported versions.
  - Every result carries `resultType`: `"complete"` or `"input_required"`.
    Clients treat an absent `resultType` as `"complete"`.

### Talking to both eras

From the backward-compatibility sections of `2026-07-28/basic/versioning`
and the two transport pages:

- **stdio:** send `server/discover` first, with the preferred modern
  version in `_meta`.
  - A `DiscoverResult` means modern.
  - A recognized modern error (`-32022`) means modern: retry with a
    supported version, and do **not** fall back.
  - Any other error, or no response within a reasonable timeout, means
    legacy: fall back to `initialize`.
  - The fallback must not be keyed to one specific error code. Legacy
    servers answer unknown pre-`initialize` requests with various codes,
    or not at all.
- **Streamable HTTP:** send a modern request.
  - Success, or a `400` whose body is a recognized modern error, means modern.
  - Any other `4xx` means legacy: send `initialize`.
  - If that fails too, try the deprecated HTTP+SSE transport.
- Cache the result per server process (stdio) or per origin (HTTP).
- A dual-era *server* picks its behaviour from how the client opens:
  modern `_meta` gets stateless handling, and `initialize` gets the legacy
  semantics for that process or session.

## Server-to-client interaction

- **Handshake era:** the server sends ordinary JSON-RPC requests to the
  client (`roots/list`, `sampling/createMessage`, `elicitation/create`,
  and `ping`).
  - Over Streamable HTTP these arrive on the POST's SSE response stream or
    on the GET listening stream.
  - The client replies with a POST.
- **2026-07-28, Multi Round-Trip Requests (SEP-2322):**
  - Servers MUST NOT send requests at all.
  - `tools/call`, `prompts/get` or `resources/read` may instead return
    `{resultType: "input_required", inputRequests: {key: {method, params}}, requestState}`.
  - The client fulfils each entry, then retries the original request under
    a **new** request ID, with `inputResponses: {key: result}` and the
    opaque `requestState`, which it returns unmodified.
- Roots, Sampling and Logging are deprecated as of 2026-07-28 (SEP-2577),
  with removal no earlier than 2027-07-28.

## Transports

### stdio

- The client launches the server as a subprocess.
- Messages are newline-delimited JSON-RPC, with no embedded newlines.
- stdout carries only MCP messages.
- stderr is for logging. Since 2025-11-25, any logging is allowed there,
  and clients SHOULD NOT treat stderr output as an error.
- Batches (JSON arrays) were allowed only in 2025-03-26.
- Shutdown: close the server's stdin, then send SIGTERM, then SIGKILL.

### HTTP+SSE (2024-11-05; deprecated since 2025-03-26)

- The client GETs the SSE endpoint.
- The server's first event is `endpoint`, whose data is the URI for POSTs.
- Every client message is POSTed to that URI.
- Every server message (responses included) arrives as an SSE `message`
  event on the GET stream.
- When following the `endpoint` URI, the client must stay on the same
  origin, or a server could steer it at internal hosts (SSRF).

### Streamable HTTP (2025-03-26 onwards)

- There is one MCP endpoint.
- **POST:**
  - The client sends `Accept: application/json, text/event-stream`.
  - A body carrying only notifications or responses gets `202 Accepted`.
  - A body carrying a request gets either `application/json` or an SSE
    stream, which may hold related notifications and requests before the
    response.
- **GET** (handshake era only):
  - Opens an optional listening stream for unsolicited server messages.
  - The server returns `405` if it doesn't support one.
  - Removed in 2026-07-28 in favour of `subscriptions/listen`.
- **Sessions** (2025-03-26 to 2025-11-25):
  - The server may assign `Mcp-Session-Id` on the response that carries
    the `InitializeResult`.
  - Session IDs are visible ASCII only (0x21–0x7E).
  - The client echoes the ID on every later request.
  - `404` means the session expired, and the client must re-initialize.
  - `DELETE` ends the session.
  - Removed in 2026-07-28. State lives in server-minted handles passed as
    tool arguments.
- **`MCP-Protocol-Version` header:**
  - Required from 2025-06-18 on.
  - A server that sees no header may assume `2025-03-26`.
  - An unsupported value gets `400`.
  - In 2026-07-28 it must match the `_meta` version, or the server returns
    `400` with `HeaderMismatch` (`-32020`).
- **`Mcp-Method` / `Mcp-Name` headers** (2026-07-28, SEP-2243):
  - Required on every POST.
  - `Mcp-Name` carries `params.name` or `params.uri` for `tools/call`,
    `prompts/get` and `resources/read`.
  - Values that are not plain ASCII use `=?base64?…?=`.
  - Tool parameters marked `x-mcp-header` become `Mcp-Param-*` headers.
- **Resumability:**
  - 2025-03-26 to 2025-11-25: SSE event `id`, then resume with GET and
    `Last-Event-ID`.
  - 2025-11-25 adds a priming event (an `id` with empty data) and the
    `retry` field, which lets servers close connections and have clients
    poll (SEP-1699).
  - **Removed** in 2026-07-28: a broken stream loses its request, and the
    client re-issues it under a new ID.
- **Origin:** servers MUST validate `Origin`. Since 2025-11-25 an invalid
  origin gets `403`.
- **JSON-RPC batching** in POST bodies was valid only in 2025-03-26.

## Discovery

- **`server/discover`** (2026-07-28): live discovery of versions and
  capabilities over the MCP connection itself.
- **Server Cards** (SEP-2127, an extension, not core):
  - The recommended location is `<streamable-http-url>/server-card`.
  - The only well-known path defined is `/.well-known/ai-catalog.json`, an
    AI Catalog that may link to or embed cards.
  - Cards give identity, endpoints and `supportedProtocolVersions`; they
    do not list tools.
  - When a card and `server/discover` disagree, `server/discover` wins.
- **There is no official `/.well-known/mcp`.**
  - Earlier versions of these notes cited proposals for it (SEP-1649,
    SEP-1960) and for DNS discovery (SEP-1959). Those were superseded by
    SEP-2127.
  - Third-party drafts with other paths (`/.well-known/mcp-server`, DNS
    SRV/TXT) exist but are unofficial.
- **OAuth Protected Resource Metadata** (RFC 9728): see below.

## Authorization (HTTP only)

stdio servers take credentials from the environment instead.

- **2024-11-05:** no authorization spec.
- **2025-03-26:** OAuth 2.1 with PKCE.
  - The client discovers RFC 8414 metadata at
    `{origin}/.well-known/oauth-authorization-server`.
  - Without metadata, it falls back to `/authorize`, `/token` and
    `/register`.
  - Dynamic Client Registration is SHOULD.
- **2025-06-18:**
  - MCP servers are OAuth resource servers and MUST publish Protected
    Resource Metadata (RFC 9728) listing `authorization_servers`.
  - A `401` carries `WWW-Authenticate: Bearer resource_metadata="…"`.
  - Clients send `resource=<canonical MCP URI>` (RFC 8707) in both the
    authorization and token requests.
  - Servers validate the token audience; passing tokens through is
    forbidden.
- **2025-11-25:**
  - PRM can also be discovered at
    `/.well-known/oauth-protected-resource/<path>`, then at the root.
  - Clients must support both RFC 8414 and OIDC discovery. For an issuer
    with a path, they try, in order:
    `/.well-known/oauth-authorization-server/<path>`,
    `/.well-known/openid-configuration/<path>`,
    `/<path>/.well-known/openid-configuration`.
  - Scope comes from the `WWW-Authenticate` `scope` if present, otherwise
    from PRM `scopes_supported`, otherwise it is omitted.
  - A `403 insufficient_scope` triggers step-up (SEP-835).
  - Client registration priority: pre-registered, then Client ID Metadata
    Documents (an HTTPS `client_id` URL, SEP-991), then DCR (now MAY).
  - Clients must refuse to proceed if `code_challenge_methods_supported`
    is missing.
- **2026-07-28:**
  - Clients validate `iss` in the authorization response (RFC 9207,
    SEP-2468).
  - Credentials are keyed by issuer, and the client re-registers if the
    authorization server changes (SEP-2352).
  - DCR is deprecated in favour of CIMD, and clients send
    `application_type`.
- **Extensions (ext-auth):** client credentials (SEP-1046),
  enterprise-managed authorization (SEP-990), DPoP, WIF jwt-bearer.
- Status codes:
  - `401`: token missing or invalid
  - `403`: insufficient scope (or invalid `Origin`)
  - `400`: malformed request

## TLS

- TLS 1.2 is the minimum; 1.3 is recommended.
- All authorization endpoints MUST use HTTPS.
- Redirect URIs must be `localhost` or HTTPS.

## Redirects

- OAuth redirect URIs must exactly match the registered values.
- MCP defines no behaviour for HTTP redirects on the MCP endpoint itself.
  mcp-test-driver refuses to follow them, as a defence against SSRF.

## Security considerations for clients

- Treat every server-supplied string as untrusted: tool names,
  descriptions, `instructions`, URLs, stderr, and session IDs.
- Never key authorization to a session ID.
- Keep metadata fetches, the legacy SSE `endpoint` and the URLs in
  URL-mode elicitation on expected origins, and never open them
  automatically.
- See [security-model.md](security-model.md).

## Key references

- MCP spec source: https://github.com/modelcontextprotocol/modelcontextprotocol
- Rendered spec: https://modelcontextprotocol.io/specification/2026-07-28
- Changelogs: `docs/specification/<rev>/changelog.mdx`, vendored as
  [specs/&lt;rev&gt;/changelog.mdx](specs/)
- Deprecated features registry: `docs/specification/2026-07-28/deprecated.mdx`
- SEP index: https://modelcontextprotocol.io/seps
- Conformance suite: https://github.com/modelcontextprotocol/conformance
- RFCs:
  - 9728 (Protected Resource Metadata)
  - 8414 (AS Metadata)
  - 8707 (Resource Indicators)
  - 7591 (Dynamic Client Registration)
  - 9207 (`iss` parameter)
  - 8252 (native apps / loopback redirect)
  - 8628 (device grant)
- OAuth 2.1: draft-ietf-oauth-v2-1
