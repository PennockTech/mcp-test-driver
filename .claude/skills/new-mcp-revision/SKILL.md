---
name: new-mcp-revision
description: Add a newly released MCP specification revision to mcp-test-driver, or refresh the vendored schemas in docs/specs. Use when a new dated spec revision ships, when the draft changes, or when test_versions.py fails after a spec refresh.
---

# Add an MCP revision, or refresh the vendored specs

`docs/specs/` is vendored upstream data: never edit it by hand.  The
registry in `src/mcp_test_driver/versions.py` is checked against it by
`tests/test_versions.py`.  Background:
`docs/decisions/0003-revision-registry.md`.

## Refresh only

```sh
scripts/fetch-specs.sh              # tip of each upstream main
scripts/fetch-specs.sh <sha> <sha>  # pin spec and conformance commits
uv run pytest tests/test_versions.py
```

Needs network access to `github.com` and `raw.githubusercontent.com`.
`docs/specs/SOURCES` records what was fetched.  If the tests fail, the
registry is out of date: go to step 3.  If
`test_draft_matches_latest_or_needs_new_revision` fails, the draft has
gained a version string or methods, and a new revision is due.

## New revision

1. In `scripts/fetch-specs.sh`, add the date to `VERSIONS` and, if the
   conformance suite published a requirements file, to `CONF_FILES`.
2. Run the script.  Read `docs/specs/<rev>/changelog.mdx`, then diff
   `schema.ts` against the previous revision for anything the changelog
   omits.
3. In `versions.py`, add `V<rev> = _evolve(V<prev>, "<rev>", add={...},
   remove={...})`, mirroring the changelog: methods by direction,
   capabilities, content types, `Feature` flags (add enum members for
   new wire behaviour), deprecations, error codes.  Add it to `SPECS`;
   `KNOWN_VERSIONS`, `LATEST*` and the era lists derive from that.
4. Make the wire behaviour real wherever a new `Feature` matters: framing
   in `protocol.py`, headers in `transport/`, negotiation in
   `negotiate.py`.  The revision sets defaults only; do not gate
   existing commands on it (`docs/decisions/0001-revision-never-gates.md`).
5. Add passive checks for what the revision newly requires or forbids
   (`add-check` skill).
6. Extend the fakes (`tests/fakes/engine.py`) to speak the revision, and
   add tests.  `tests/test_versions.py` is parametrized over
   `KNOWN_VERSIONS` and needs no edit.
7. Update the docs that own the facts:
   - `docs/mcp-feature-matrix.md`: a column for the revision, Driver
     column for what changed;
   - `docs/mcp-spec-notes.md`: semantics that tables cannot carry;
   - `README.md`: the list of supported revisions;
   - `docs/specs/README.md`: the revision list.
8. If the conformance suite froze a requirements list for the revision,
   check the runner version pinned in `scripts/conformance-client.sh`,
   then run `task conformance` (needs Node).
9. Run the gate, plus `uv run tox`.
