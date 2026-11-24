# Vendored MCP specifications

Upstream files, copied unmodified.  **Do not edit by hand.**  Refresh them
with:

```sh
scripts/fetch-specs.sh            # tip of each upstream main branch
scripts/fetch-specs.sh <sha> <sha>  # pin specific commits
```

[`SOURCES`](SOURCES) records the upstream commits and the fetch time.

| Path | Upstream | Contents |
|---|---|---|
| `<rev>/schema.json` | `modelcontextprotocol/modelcontextprotocol` `schema/<rev>/schema.json` | Machine-readable JSON Schema for the revision |
| `<rev>/schema.ts` | `schema/<rev>/schema.ts` | The TypeScript source the JSON is generated from, with full doc comments |
| `<rev>/changelog.mdx` | `docs/specification/<rev>/changelog.mdx` | Changes from the previous revision. 2024-11-05 has none. |
| `conformance/<rev>.yaml` | `modelcontextprotocol/conformance` `requirements/<rev>.yaml` | Frozen list of conformance scenarios required for the revision |

Revisions: `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`,
`2026-07-28`, plus `draft` (unreleased; it still declares
`LATEST_PROTOCOL_VERSION = "2026-07-28"`).

These files are reference data for humans and for tests.  They are not
shipped in the wheel.  The plan is for `tests/test_versions.py` to
cross-check `src/mcp_test_driver/versions.py` against them, so that a spec
refresh which changes the method set fails the tests until the registry is
updated.

When a new revision ships:

1. Add it to `VERSIONS` (and, if published, `CONF_FILES`) in
   `scripts/fetch-specs.sh`.
2. Run the script.
3. Update `docs/mcp-feature-matrix.md` and the version registry.
