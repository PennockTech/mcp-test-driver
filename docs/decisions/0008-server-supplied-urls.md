# 0008: Never follow, open or render what a server points at

Status: Accepted (2026-11-12)

## Context

Servers hand the client URLs and documents: HTTP redirects, the
HTTP+SSE `endpoint`, OAuth metadata locations, server cards, URL-mode
elicitation, MCP Apps HTML.  Each is a way to make the client reach an
internal host (SSRF), send credentials elsewhere, or run content on the
user's machine.

## Decision

- **Redirects** on the MCP endpoint and on fetches are never followed; a
  3xx is an error.
- **The HTTP+SSE `endpoint`** must be the same origin as the SSE URL, or
  the connection is refused: our POSTs and `Authorization` would follow
  it.
- **Fetched documents** go through `fetch.JsonFetcher`: GET only, no
  redirects, HTTPS except to loopback, a 1 MiB cap, no credentials unless
  the caller passes them.
- **URLs for the user** (elicitation, authorization) are printed,
  sanitized, with the host shown.  A browser opens only for the user's
  own `--browser` login.
- **HTML** (MCP Apps) is never rendered; `/app save` writes it to a file
  the user names.
- **Server cards** are fetched only on request (`/server-card`).

## Consequences

- A server can still make the client GET a public HTTPS URL of its
  choosing.  It cannot reach plain-HTTP internal services or chain
  redirects.
- Servers that rely on redirects fail with a clear error; that is
  accepted.
