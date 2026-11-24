---
name: add-check
description: Add a conformance check or finding to mcp-test-driver - an active probe or /check entry in probe.py, or a passive check in checks.py that records a finding. Use when asked to detect, flag or test a server behaviour.
---

# Add a conformance check

First decide which kind it is.

| Kind | When it runs | Where |
|---|---|---|
| Passive check | On traffic the user causes anyway | `checks.py`, called from the session |
| Probe | `/probe`, `--probe`: does the server support X, whatever it claims? | `PROBES` in `probe.py` |
| Check | `/check`, `--check`: does the server handle X correctly? | `CHECKS` in `probe.py` |

Read `docs/decisions/0001-revision-never-gates.md` and
`0012-probes-and-checks.md` first.  In short: findings annotate and never
block; probes never call a server's real tools.

## Passive check (`checks.py`)

1. Compare the observation with the claimed revision via `versions.py`
   (`spec.has(Feature.X)`, `introduced_method()`, `removed_method()`),
   never by comparing version strings.
2. Record on the session's `DiagnosticLog` (`session.findings`) with
   the method for its kind, each taking `(code, message, spec_ref)`:
   - `.violation()`: breaks a requirement of the claimed revision;
   - `.beyond()`: defined by a later revision or an extension;
   - `.deprecated()`: deprecated at the claimed revision;
   - `.note()`: anything else worth knowing.
3. `code` is a stable kebab-case name; `spec_ref` is the page path under
   the revision, e.g. `basic/versioning`.  Sanitize any server text in
   the message.

## Probe or check (`probe.py`)

1. Write `def check_x(ctx: Context) -> Result`.  Use `_call(ctx, ...)`
   to send, and return a `Result(id, title, Outcome.…, detail)`.
   `Outcome.SKIP` when it does not apply (for example `not ctx.http`).
2. **ID:** if the official suite has a scenario for the same behaviour,
   use its name; the lists are in `docs/specs/conformance/<rev>.yaml`.
   Otherwise choose a kebab-case ID.
3. Target only things that cannot have side effects: the `_NO_SUCH_*`
   names, list methods, malformed input.
4. Append it to `PROBES` or `CHECKS`.  `run()` catches `McpError` and
   `TransportError`; `record()` turns results into findings.

## Tests

`tests/test_checks.py` or `tests/test_probe.py`.  Drive a fake server
whose scenario provokes the behaviour, once conforming and once not; add
a scenario key to `tests/fakes/` if none exists.

## Docs

If the check corresponds to a row in `docs/mcp-feature-matrix.md`,
update its Driver column.  Then run the gate.
