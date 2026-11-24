# Roadmap

Every released MCP revision from 2024-11-05 to 2026-07-28 is supported;
[mcp-feature-matrix.md](mcp-feature-matrix.md) says how, feature by
feature.  This file lists what is not done.  It is the only such list.

## Not implemented

- **OAuth extensions beyond client credentials:** enterprise-managed
  authorization (SEP-990, ID-JAG), DPoP, `private_key_jwt`, WIF
  jwt-bearer.  Discovery shows what an authorization server offers.
  The key-based ones need a crypto dependency
  ([decision 0007](decisions/0007-runtime-dependencies.md)).
- **Skills:** `resources/directory/read` has no command; use `/send`.
- **Sampling:** the `sampling.tools` and `sampling.context` capabilities
  are not declared.  A `!json` reply can still carry `tool_use`.
- **SOCKS proxies.**
- **DNS-based server discovery:** only unofficial third-party drafts
  exist.

## Possible future work

- Compare `--check` verdicts with the official conformance suite's
  *server* leg automatically.  Done by hand so far: verdicts agree where
  scenarios overlap.
- Declare `jsonschema` as an optional extra (`validate`).  Today it is
  used only when already installed.
- The next dated spec revision is targeted for 2027-03-30 (SEP-3398);
  see the `new-mcp-revision` skill.
