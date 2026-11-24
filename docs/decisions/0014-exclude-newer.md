# 0014: `exclude-newer = "3 days"` stays a relative duration

Status: Accepted (2026-04-07)

## Context

`[tool.uv] exclude-newer` keeps packages published in the last three
days out of dependency resolution.  Current
`uv` accepts a duration and writes the resolved timestamp into `uv.lock`.
Older `uv`, and older `ruff` / `ty` sharing its TOML parser, warn
*"failed to parse year in date '3 days'"*.

## Decision

Keep the duration.  The warning means the local tooling is stale, not
that the configuration is wrong.

## Consequences

- Do not rewrite it as a date.  Upgrade `uv` / `ruff` / `ty`, or ignore
  the warning: it blocks nothing.
- If stale tooling rewrote `uv.lock` during `uv sync`, restore it with
  `git checkout -- uv.lock`.
