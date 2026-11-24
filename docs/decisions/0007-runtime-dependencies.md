# 0007: Runtime dependencies are `gnureadline` and `urllib3` only

Status: Accepted (2026-04-01; extended 2026-11-12)

## Context

A debugging tool run with `uvx` should install fast and rarely break.
Three dependencies were weighed.

- **urllib3.**  The standard library could replace it: `http.client`
  streams responses, never follows redirects, and a retry loop is a few
  lines.  What it lacks is separate connect and read timeouts (only a
  fragile `sock.settimeout()` after connect) and, since 0006, proxy
  routing with CONNECT tunnels and proxy TLS, which `net.py` gets from
  `ProxyManager`.
- **gnureadline.**  macOS ships libedit as `readline`, with different
  binding syntax and missing hooks.
- **jsonschema.**  Needed only to validate `structuredContent` against a
  tool's `outputSchema`.

## Decision

- Keep `urllib3`.  Going zero-dependency is not a goal; revisit only if
  it becomes one.
- Depend on `gnureadline` and prefer it; fall back to stdlib `readline`
  with a warning when it is libedit.
- Do not depend on `jsonschema`.  Use it when it is importable; otherwise
  record a note that the validation was skipped.
- OAuth uses the standard library plus `urllib3`: no OAuth or crypto
  package.

## Consequences

- A new runtime dependency needs a new decision.
- Flows needing asymmetric crypto (`private_key_jwt`, DPoP) are out of
  reach without one; see [../roadmap.md](../roadmap.md).
- `completion.py` keeps its libedit branches.
