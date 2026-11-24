# 0012: Probes are safe to aim at any server; check IDs follow the official suite

Status: Accepted (2026-11-12)

## Context

`/probe` and `/check` are run against servers the user may not own, and
tools can have side effects.  Separately, the official conformance suite
publishes named scenarios, but its server scenarios assume its own
reference fixture and it drives stdio servers only as a client.

## Decision

- Probes and checks never call a server's own tools.  The only
  `tools/call` names a tool that does not exist; the same goes for
  resources and methods (`_NO_SUCH_*` in `probe.py`).
- Checks are generic: they run against any server, stdio included.
- A check that tests what an official scenario tests uses that
  scenario's name as its ID (lists are in `docs/specs/conformance/`), so
  verdicts can be compared.
- `--check` exits 1 on failures and 2 when the server is unreachable.

## Consequences

- A check that needs a working tool call cannot exist here; suggest the
  user script it with `-c`.
- The driver's own client conformance is tested the other way round, by
  the official runner (`task conformance`).
