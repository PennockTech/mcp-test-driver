# 0016: Eras and transports are named for how they work, not how old they are

Status: Accepted (2026-11-25)

## Context

The two protocol eras were first called "legacy" and "modern", with a
third spelling, "handshake", inside the code, and the 2024-11-05 HTTP+SSE
transport was the "legacy SSE" transport.  Names that encode age go stale
the moment the protocol changes again: the next form makes "modern" wrong,
and one day Streamable HTTP will be the old transport.  No release had
shipped these names, so they could change freely.

## Decision

- The eras are **`handshake`** (2024-11-05 to 2025-11-25: one `initialize`
  negotiation per connection or session) and **`stateless`** (2026-07-28
  onwards: version and capabilities in every request's `_meta`).  The
  same two words are used in the CLI, the code, output, findings, test
  scenarios and docs.  In prose, a server is "handshake-era" or
  "stateless-era".
- The 2024-11-05 transport is **HTTP+SSE** (`http_sse`, `--transport
  sse`), as the spec calls it.
- No era, revision, transport or feature is *named* by its age: not
  legacy, modern, old, new, classic or next, in identifiers, option
  values, finding codes or docs.  A thing tied to a revision is named for
  that revision or its era (`RESOURCE_NOT_FOUND_HANDSHAKE`).  Comparing
  two revisions ("a later revision", "older than offered") is fine.
- "Era" and "dual-era" stay: neither says which side is newer.
- `--era` takes names only.  A year is not accepted as an alias: an era
  spans several years, and a year reads as a choice of revision.  The
  exact time input is `--protocol-version`, and a known revision selects
  its era.

## Consequences

- A third era gets its own descriptive name; these two stay true.
- `--help` shows each era's revision range, built from `versions.py`.
- This file is the only place the retired words appear outside the
  vendored specs.
