# OAuth

Client side of the MCP authorization spec (2025-06-18 onwards, with
2025-03-26 fallbacks).  The spec rules by revision are in
[../mcp-spec-notes.md](../mcp-spec-notes.md) ("Authorization"); the
defences are in [security.md](security.md).  Code: `auth_diag.py`
(discovery), `oauth.py` (client), `fetch.py` (metadata fetches),
`commands/web.py` (`/auth`).  Standard library plus `urllib3`; no OAuth
or crypto dependency.

## Sequence

1. **Trigger.**  A 401 raises `AuthRequiredError`.  With `--oauth` (or
   `/auth login`) the transport's `AuthProvider` handles the challenge and
   the request is retried with the new token.
2. **Discovery** (`auth_diag.py`), every step recorded for display:
   - Protected Resource Metadata: the `WWW-Authenticate`
     `resource_metadata` URL, else the path-inserted well-known URI, else
     the root one.
   - Authorization-server metadata: RFC 8414, then OpenID Connect, in the
     spec's order for issuers with and without a path.
3. **Client registration**, in the spec's priority order:
   pre-registered (`--client-id`, with `--client-secret-env` for
   confidential clients), then a Client ID Metadata Document (ours, or
   `--client-metadata-url`), then Dynamic Client Registration with
   `application_type: native`.
4. **Flow** (`--auth-flow`):
   - `code` (default): authorization code with PKCE S256 and a loopback
     redirect.  The URL is printed; pasting the final redirect URL at the
     prompt works when the browser is on another machine.
   - `client-credentials`: machine to machine, needs a client secret.
   - `device`: RFC 8628, not MCP-specified; offered when the AS has it.
5. **Scope**: the challenge's `scope`, else `scopes_supported`, else
   omitted; plus `offline_access` when offered.  `--scope` overrides.
6. **Afterwards**: expired tokens are refreshed; a 403
   `insufficient_scope` triggers step-up, with a retry limit.

Token-endpoint client authentication: `none`, `client_secret_basic`,
`client_secret_post`.

## Findings versus refusals

Every deviation is a finding.  The flow stops only where continuing would
send credentials to the wrong party
([decision 0009](../decisions/0009-oauth-refusals.md)).

## Client identity

The default `client_id` is the URL of the document in
`site/oauth/client.json`, published by GitHub Pages; `oauth.CIMD_CLIENT_ID`
and `tests/test_site.py` tie the two together.  The loopback listener
tries port 33418, then any free port (`--callback-port`).  Why the URL and
redirect URIs are fixed:
[decision 0010](../decisions/0010-oauth-client-identity.md).

## Tokens

In memory by default.  `--token-cache PATH` persists them, keyed by
server and recording the issuer, so a changed authorization server means
a new registration (2026-07-28).

## Testing

`tests/fakes/auth_server.py` is a configurable authorization server
beside the HTTP fake (scenario key `oauth`).  The official conformance
suite's `auth/*` client scenarios run under `task conformance`.

## Not implemented

See [../roadmap.md](../roadmap.md).
