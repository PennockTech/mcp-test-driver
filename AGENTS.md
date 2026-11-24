# AGENTS.md

`mcp-test-driver` is an interactive REPL and diagnostic driver that people
use to test MCP servers over stdio or HTTP.  It is not an MCP client for
an LLM.  Python 3.12+, src layout (`src/mcp_test_driver/`), managed by `uv`.

This file holds only what every change needs.  Everything else is reached
through [docs/README.md](docs/README.md), a short index; read it before
changing `src/`, and open further files only when the task touches them.

## Done means

All three pass, always through `uv run` (never system `python`, `pytest`,
`ruff` or `ty`):

```sh
uv sync --group dev   # once per environment
uv run ruff check
uv run ty check
uv run pytest
```

Changes to transport, protocol or completion code also run `uv run tox`.

## Rules that must hold

The reasons are in the numbered decisions under `docs/decisions/`.

1. **Server text is untrusted.**  Nothing a server sent reaches the
   terminal or readline except through `sanitize.sanitize` /
   `sanitize.safe_json`. (0004)
2. **The negotiated revision never gates a feature.**  It picks defaults
   and labels findings; the user can always send anything. (0001)
3. **Only the main thread prints.**  Reader threads queue. (0005)
4. **HTTP clients come only from `net.HttpClient`**, and the redirect,
   same-origin and fetch limits stay. (0006, 0008)
5. **JSON-RPC IDs are never reused or guessed.**
6. **Generated and vendored files are not hand-edited:** `docs/specs/`
   (run `scripts/fetch-specs.sh`), `uv.lock` (let `uv` write it).
7. **`exclude-newer = "3 days"` in `pyproject.toml` is correct.**  A parse
   warning about it means stale tooling; if that rewrote `uv.lock`, run
   `git checkout -- uv.lock`. (0014)
8. **Never change the version or create a tag**, and never skip hooks or
   signing. (0013)

## Conventions

- Work on a feature branch.  Commit subjects are
  [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`,
  `fix(oauth):`, `docs:`, …); the release version is computed from them.
  Messages say why.
- Types: `X | Y`, PEP 695 generics, a `Protocol` plus `cast()` for
  stub-less modules.  No `Any` where a concrete type fits, no bare
  `object` returns.
- Comments say why, not what.  Leave untouched code's docstrings and
  annotations alone.
- New behaviour gets a test in `tests/test_<module>.py`; see
  [tests/AGENTS.md](tests/AGENTS.md).
- One fact lives in one place.  When behaviour changes, update the doc
  that owns it (the index says which) rather than adding another.

## Where to look

| For | Read |
|---|---|
| Module map, message flow, threads | [docs/designs/architecture.md](docs/designs/architecture.md) |
| Any other design, decision or MCP spec fact | [docs/README.md](docs/README.md) |
| Writing or running tests | [tests/AGENTS.md](tests/AGENTS.md) |
| Step-by-step procedures (run the driver against a fake, add a command, add a check, add a spec revision) | `.claude/skills/*/SKILL.md` |
