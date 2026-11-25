# Era and revision negotiation

What the spec requires is in [../mcp-spec-notes.md](../mcp-spec-notes.md)
("Eras").  This file is what the driver does with it.  Code:
`negotiate.py`, `versions.py`, `protocol.py`, `transport/base.py`
(`WireProfile`).

## Terms

- **Era**: `handshake` (2024-11-05 to 2025-11-25, `initialize`) or
  `stateless` (2026-07-28 onwards, per-request `_meta`).  The names say
  how each works, never which is newer
  ([decision 0016](../decisions/0016-era-names.md)).
- **Revision**: a dated spec version.  `versions.SPECS` holds one
  `SpecVersion` per revision, each derived from its predecessor by
  `_evolve()` so the code mirrors the changelog.
- **Wire profile**: the framing defaults a revision implies: handshake or
  `_meta`, which HTTP headers, whether a session ID is sent.

## Connecting (`negotiate.connect`)

With `--era auto`:

1. Send `server/discover` framed as the latest stateless revision (or
   `--protocol-version`, if stateless), waiting `--probe-timeout` (5 s).
2. A `DiscoverResult` means stateless: adopt the requested revision, or
   the highest mutual one from `supportedVersions`.
3. `UnsupportedProtocolVersion` (`-32022`) means stateless: retry once
   with a revision from `data.supported`.
4. Anything else means handshake: another error, an HTTP 4xx without a
   stateless-era error body, silence, or the connection closing.  A stdio
   server that exited on the probe is restarted, with a finding.
5. Handshake: `initialize`, offering the latest handshake revision (or
   `--protocol-version`).  A counter-offer is accepted; an unknown
   revision or an alias (`2024-10-07`) is used best-effort with a finding.
6. If `initialize` over Streamable HTTP gets 400, 404 or 405, switch to
   the 2024-11-05 HTTP+SSE transport and `initialize` again.  The SSE
   `endpoint` must be same-origin.
7. Handshake era over Streamable HTTP: open the GET listening stream
   straight away (`--no-listen` skips it), because real servers send
   requests there.

`--era handshake` starts at step 5; `--era stateless` fails instead of
falling back; `--transport sse` skips the probe.  Every step is appended
to `ServerState.how_chosen`, which the connection report prints.

The reasoning is in [decision 0002](../decisions/0002-dual-era-negotiation.md).

## What the revision controls

Only defaults and labels ([decision 0001](../decisions/0001-revision-never-gates.md)):

- framing: `initialize` state or per-request `_meta`; the
  `MCP-Protocol-Version` header from 2025-06-18; `Mcp-Method`, `Mcp-Name`
  and `Mcp-Param-*` in the stateless era; `Mcp-Session-Id` only in the
  handshake era;
- which mechanism a command uses by default: `/loglevel` sets the
  `_meta` log level in the stateless era (`--rpc` sends `logging/setLevel`
  anyway); `/listen` uses `subscriptions/listen` or the GET stream;
- how a finding is labelled: `violation`, `beyond-claimed`, `deprecated`
  or `note`, by comparing what was seen with the claimed revision
  (`versions.introduced_*`, `removed_method`).

Sending a method the revision lacks is allowed and recorded as a note.

## Multi round-trip requests (stateless era)

`tools/call`, `prompts/get` and `resources/read` may answer with
`resultType: "input_required"`.  The session answers each `inputRequests`
entry through the handler registry and retries under a new request ID
with `inputResponses` and the unmodified `requestState`, for at most 8
rounds.  `InputRequiredResult` from any other method is a finding.

## Adding a revision

Follow the `new-mcp-revision` skill (`.claude/skills/`).
