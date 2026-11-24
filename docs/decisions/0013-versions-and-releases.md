# 0013: Only the release task changes the version or tags

Status: Accepted (2026-11-24)

## Context

The version lives in `pyproject.toml` and, as uv records it, `uv.lock`.
The release workflow publishes to PyPI on a tag push and refuses when
tag and project version disagree.  Tags are signed by the maintainer.

## Decision

- `task release:commits-and-tag`, run by the maintainer, is the only
  thing that edits the version or creates a tag.  Between releases the
  version is the next patch as a dev release (`0.2.1.dev0`).
- Commit subjects follow Conventional Commits; `convco` computes the
  next version from them.
- The package reads its version from installed metadata only.

## Consequences

- Agents and contributors never touch the version line or tag, and
  never bypass hooks or signing.
- A non-conventional commit subject degrades the computed bump to a
  patch.
- Procedure: [../releasing.md](../releasing.md).
