# 0003: Revisions are data, checked against the vendored schemas

Status: Accepted (2026-11-11)

## Context

The driver supports every dated revision (2024-11-05 onwards), and more
will ship.  Version comparisons scattered through the code would rot, and
hand-kept tables drift from the spec.

## Decision

- `versions.py` holds one `SpecVersion` per revision (methods by
  direction, capabilities, content types, feature flags, deprecations),
  each derived from its predecessor with `_evolve()`, mirroring the
  changelog.  Code asks the registry; it does not compare version
  strings.
- The upstream `schema.json`, `schema.ts`, changelogs and conformance
  requirement lists are vendored under `docs/specs/` by
  `scripts/fetch-specs.sh`, pinned to commits recorded in
  `docs/specs/SOURCES`.  They are never hand-edited.
- `tests/test_versions.py` asserts the registry's method, capability and
  content-type sets equal the schemas'.

## Consequences

- A spec refresh that changes a method set fails the tests until the
  registry is updated.  Fix the registry, not the schema.
- The unreleased `draft` is vendored and watched by a test, not
  registered as a revision.
- Adding a revision is a table entry plus a re-fetch: see the
  `new-mcp-revision` skill.
