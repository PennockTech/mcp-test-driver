# Security model

The driver connects to arbitrary MCP servers, local (stdio) and remote.
Assume the server is hostile.  Tests for these defences are in
`tests/test_security.py`.

## Threats and where they are stopped

| A malicious server tries | Defence | Code |
|---|---|---|
| Terminal injection: ANSI/OSC escapes, control characters, bidi overrides in any string, including stderr | `sanitize()` / `safe_json()` on everything displayed | `sanitize.py`, `render.py` |
| Shadowing a builtin with a tool named `/quit` | Builtins own the `/` prefix; it is stripped from tool names | `commands/__init__.py` |
| Memory exhaustion | 16 MiB cap per stdio line, HTTP body and SSE stream; 1 MiB for fetched documents; 100-page cap on lists | `transport/base.py`, `fetch.py`, `protocol.py` |
| Hanging the client | Read timeout (`--timeout`, 120 s) that holds mid-line; 10 s connect timeout | `transport/stdio.py`, `transport/http.py` |
| SSRF by redirect | Redirects are never followed; a 3xx is an error | `transport/http.py`, `fetch.py` |
| SSRF or credential theft by naming another host | The HTTP+SSE `endpoint` must be same-origin; metadata fetches are GET-only, HTTPS except loopback, with no credentials | `transport/http_sse.py`, `fetch.py` |
| Header injection through a session ID or other value | CR, LF and NUL rejected in every header; session ID restricted to visible ASCII | `transport/base.py`, `transport/http.py` |
| Response confusion with mismatched IDs | A response is matched by ID; strays are reported and never handed to a caller; IDs are never reused | `protocol.py` |
| Stealing OAuth credentials (wrong audience, issuer mix-up, CSRF) | See below | `oauth.py` |
| Running code or opening pages on the user's machine | URLs are never opened and HTML is never rendered | `interactive.py`, `commands/ext.py` |

## Displaying server text

- `sanitize()` strips ANSI and OSC sequences, control characters
  (0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F, 0x7F, 0x9B) and bidirectional
  overrides and isolates (U+202A-202E, U+2066-2069).  Newlines and tabs
  stay.
- `safe_json()` sanitizes *after* encoding: with `ensure_ascii=False`,
  `json.dumps` passes 0x9B and bidi characters through.
- `render.py` shows unknown fields rather than dropping them, all through
  those two functions.  Binary content is summarised by type and size.
- Tool names: sanitized, spaces to underscores, leading `/` stripped,
  empty names skipped.  The server's real name is kept for the call.
- Stderr lines are queued by the reader thread, then sanitized and printed
  by the main thread with a `[server stderr]` prefix.

## Network

- TLS is verified unless `--insecure`.  `--ca-bundle` only adds CAs.
- Every client is built by `net.HttpClient`, so proxy and trust settings
  cannot be bypassed; a test enforces it.
- Loopback targets are never proxied, which keeps local servers and the
  OAuth redirect listener local.  Proxy credentials go only in
  `Proxy-Authorization` and are redacted wherever the proxy URL is shown.
  An unusable proxy setting (SOCKS, a bad URL) is an error, never a
  silent direct connection.
- A proxy sees everything sent to `http://` targets, and a TLS-re-signing
  proxy sees `https://` traffic too, bearer tokens included.
- `--bearer-env` and `--client-secret-env` read secrets from the
  environment, never argv.  Trace output omits request headers;
  `redact_headers()` masks `Authorization` and cookies wherever headers
  are shown.
- HTTP 404 with an active session raises `SessionExpiredError`; `close()`
  sends DELETE for an active session.

## OAuth

- The redirect listener binds 127.0.0.1 only, accepts one request on
  `/callback`, and lives at most 5 minutes.
- `state` must match.  A present `iss` must equal the issuer (RFC 9207),
  and a missing `iss` is refused when the AS advertises it.  PKCE (S256)
  is always used.
- Protected-resource metadata naming a different `resource` is refused
  (RFC 9728 §3.3); every authorization and token request carries the
  RFC 8707 `resource` indicator.
- Tokens are never traced; `/auth status` shows only their first
  characters.  The token cache is written mode 0600 and ignored, with a
  warning, if group- or world-readable.  Entries are keyed by server and
  record the issuer.
- The authorization URL is printed, sanitized; a browser opens only with
  `--browser`.

## Interactive handlers

- Elicitation and sampling are off unless enabled; without them the
  client declares no such capability and answers "method not supported".
- URL-mode elicitation shows the URL's host separately, to expose
  look-alike links, and never opens it.
- Form mode echoes input; the spec forbids servers asking for secrets
  there.
- Tab-completion is off while answering, so server-controlled names
  cannot be completed into an answer.
- Input requested by a task goes through the same handlers and the same
  opt-in.

## Deliberate non-defences

- `/send`, `/notify` and `/raw` send whatever the user types, invalid
  JSON-RPC included.  Only the user can trigger them; nothing a server
  sends becomes a raw send.
- `/app save` writes server HTML to a file the user names, with a
  reminder to open it in a sandboxed browser profile.
- Roots are a hint to the server, not an access grant.  Root paths are
  canonicalised and added roots must be under the base path.
