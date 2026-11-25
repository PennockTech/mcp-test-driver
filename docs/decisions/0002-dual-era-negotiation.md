# 0002: Connect as a dual-era client, by probing

Status: Accepted (2026-11-12)

## Context

2026-07-28 removed the `initialize` handshake and sessions.  The spec's
backward-compatibility sections define a dual-era client: try the
stateless protocol, fall back to `initialize`.  Handshake-era servers answer an
unknown pre-`initialize` request in many ways: assorted error codes,
silence, or exiting.

## Decision

`--era auto` is the default.  The driver probes with `server/discover`,
falls back to `initialize`, and from Streamable HTTP falls back again to
the 2024-11-05 HTTP+SSE transport on 400/404/405.  The steps are in
[../designs/negotiation.md](../designs/negotiation.md).

- The fallback is never keyed to one error code.  Only a
  `DiscoverResult` or a recognised stateless-era error means "stateless".
- A stdio server that exits on the probe is restarted, with a finding.
- In the handshake era over Streamable HTTP, the GET listening stream is
  opened right after `initialize`, as SDK clients do: the official
  conformance suite showed servers sending requests there.
- Every step is recorded in `how_chosen` and shown in the report.

## Consequences

- Start-up against a silent handshake-era stdio server costs the probe
  timeout (5 s); `--era handshake` avoids it.
- `--era stateless` and `--era handshake` exist so each path can be
  tested alone.
