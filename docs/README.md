# Documentation index

Read this file; open the others only when the task touches them.

## Designs: how it works now

A module's own docstring is its design.  These cover what spans modules.

| File | Read before changing |
|---|---|
| [designs/architecture.md](designs/architecture.md) | anything, if you don't know which module owns it |
| [designs/negotiation.md](designs/negotiation.md) | `negotiate.py`, `versions.py`, wire framing in `protocol.py` or `transport/` |
| [designs/security.md](designs/security.md) | anything that prints, parses or fetches what a server supplied |
| [designs/oauth.md](designs/oauth.md) | `oauth.py`, `auth_diag.py`, `site/oauth/client.json` |

## Decisions: why, and what not to undo

Each line is the rule; the file has the reasoning.  Follow them unless a
new decision supersedes one.

| # | Decision |
|---|---|
| [0001](decisions/0001-revision-never-gates.md) | The negotiated revision sets defaults and labels findings; it never hides or blocks a feature. |
| [0002](decisions/0002-dual-era-negotiation.md) | Connect by probing `server/discover`, then `initialize`, then HTTP+SSE; never key the fallback to one error code. |
| [0003](decisions/0003-revision-registry.md) | Revisions are data in `versions.py`, cross-checked by tests against the vendored schemas in `docs/specs/`. |
| [0004](decisions/0004-untrusted-server-text.md) | All server-supplied text is sanitized before display; builtins own the `/` prefix. |
| [0005](decisions/0005-main-thread-prints.md) | Reader threads only queue; the main thread does all printing. |
| [0006](decisions/0006-one-http-client.md) | `net.HttpClient` is the only builder of HTTP clients; one proxy policy, one added CA bundle. |
| [0007](decisions/0007-runtime-dependencies.md) | Runtime dependencies are `gnureadline` and `urllib3` only; `jsonschema` is used when present. |
| [0008](decisions/0008-server-supplied-urls.md) | Never follow, open or render what a server points at, beyond a narrow GET. |
| [0009](decisions/0009-oauth-refusals.md) | OAuth deviations are findings; refuse only where continuing would leak credentials. |
| [0010](decisions/0010-oauth-client-identity.md) | The OAuth `client_id` URL and its redirect URIs are fixed. |
| [0011](decisions/0011-fakes-not-mocks.md) | Tests drive scenario-configured fake servers end to end, not mocked transports. |
| [0012](decisions/0012-probes-and-checks.md) | Probes never call a server's own tools; check IDs reuse the official conformance scenario names. |
| [0013](decisions/0013-versions-and-releases.md) | Only the maintainer's release task changes the version or tags; Conventional Commits pick the number. |
| [0014](decisions/0014-exclude-newer.md) | `exclude-newer = "3 days"` stays a relative duration. |
| [0015](decisions/0015-human-in-the-loop.md) | No LLM is wired in; client capabilities are opt-in and answered by the person at the terminal. |
| [0016](decisions/0016-era-names.md) | Eras are `handshake` and `stateless`, the transport is HTTP+SSE: names say how a thing works, never how old it is. |

Record a new decision when a change sets a rule that later work must
respect, or reverses one above: copy
[decisions/template.md](decisions/template.md), take the next number, add
a line here.  Supersede, don't rewrite, an accepted decision.

## Reference: MCP spec facts

| File | Holds |
|---|---|
| [mcp-feature-matrix.md](mcp-feature-matrix.md) | Tables: which method, capability, feature and header exists in which revision, and what the driver does with it |
| [mcp-spec-notes.md](mcp-spec-notes.md) | Prose: era rules, transports, discovery and authorization, by revision |
| [specs/](specs/README.md) | Vendored upstream schemas, changelogs and conformance lists; authoritative, never hand-edited |

## For people

| File | Holds |
|---|---|
| [user-guide.md](user-guide.md) | Using the tool, beyond the README and `--help` |
| [roadmap.md](roadmap.md) | What is not implemented, and possible future work |
| [releasing.md](releasing.md) | The maintainer's release procedure |

## Keeping this lean

- One fact, one place.  Link instead of restating.
- Options and commands are documented by `--help` and `/help` only.
- Describe the present.  History belongs in git and in decisions.
- A new file needs a line in this index.
