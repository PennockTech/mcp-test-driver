# 0001: The negotiated revision never gates a feature

Status: Accepted (2026-11-12)

## Context

Servers claim one MCP revision but often implement pieces of others:
methods from a later revision, methods a later revision removed, vendor
extensions.  A test tool that refuses what the claimed revision lacks
cannot find any of that, and cannot test how a server handles bad input.

## Decision

The negotiated revision decides two things only:

- **defaults**: the wire framing, which headers are sent, which mechanism
  a command uses first;
- **labels**: how a finding is classified (`violation`, `beyond-claimed`,
  `deprecated`, `note`).

Every command and method is always available.  A method the revision
does not define is sent anyway and recorded as a note.  Everything a
server returns is shown, unknown and newer fields included.

The only refusals are the OAuth ones in
[0009](0009-oauth-refusals.md), where continuing would leak credentials.

## Consequences

- Never write `if revision < X: refuse`.  Look features up in
  `versions.py` to choose a default or word a finding.
- New commands for a revision-specific feature work in every era.
- Passive checks compare observations against every known revision, not
  only the negotiated one.
