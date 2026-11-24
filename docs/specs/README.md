# Vendored MCP specifications

Upstream files, copied unmodified.  **Never edit them by hand.**  Refresh
with `scripts/fetch-specs.sh` (see the `new-mcp-revision` skill in
`.claude/skills/`); [`SOURCES`](SOURCES) records the upstream commits and
the fetch time.

| Path | Upstream | Contents |
|---|---|---|
| `<rev>/schema.json` | `modelcontextprotocol/modelcontextprotocol` `schema/<rev>/schema.json` | JSON Schema for the revision |
| `<rev>/schema.ts` | `schema/<rev>/schema.ts` | The TypeScript source of that schema, with full doc comments |
| `<rev>/changelog.mdx` | `docs/specification/<rev>/changelog.mdx` | Changes from the previous revision (2024-11-05 has none) |
| `conformance/<rev>.yaml` | `modelcontextprotocol/conformance` `requirements/<rev>.yaml` | Frozen list of conformance scenarios required for the revision |

Revisions: `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`,
`2026-07-28`, plus `draft` (unreleased; it still declares
`LATEST_PROTOCOL_VERSION = "2026-07-28"`).

These files are reference data and test input; they are not shipped in
the wheel.  `tests/test_versions.py` checks
`src/mcp_test_driver/versions.py` against them, so a refresh that changes
a method or capability set fails until the registry is updated
([decision 0003](../decisions/0003-revision-registry.md)).

To find what a revision says about a type or method, search
`<rev>/schema.ts`: its doc comments carry the normative text.
