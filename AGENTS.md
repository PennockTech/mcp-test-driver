# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this is

`mcp-test-driver` is an interactive REPL for testing MCP (Model Context
Protocol) servers over stdio or HTTP.  It is *not* an MCP client for an LLM
— it is a debugging tool for humans poking at MCP servers, with
tab-completion, schema introspection, and protocol tracing.

The package lives under `src/mcp_test_driver/` (src layout, built with
`uv_build`).  Tests live under `tests/` and are run with `pytest`.

## Repository layout

```
src/mcp_test_driver/
  cli.py         # argparse entry point (mcp-test-driver console script)
  repl.py        # the interactive loop, command dispatch, history
  completion.py  # readline tab-completion + help keybindings + ReadlineModule Protocol
  protocol.py    # JSON-RPC framing and MCP method bindings
  transport.py   # stdio + HTTP Streamable transport, response sanitization
  handlers.py    # client-side handlers (e.g. roots/list)
  parse.py       # command-line argument parsing for the REPL
  color.py       # ANSI color helpers
tests/           # pytest suite, mirrors src/ module-for-module
docs/            # security model, MCP spec notes, future plans
.github/workflows/  # CI (tox across Python 3.12/3.13/3.14)
Taskfile.yml     # `task` runner shortcuts (lint, typecheck, test, check)
```

## Tooling

The project is managed by [`uv`](https://docs.astral.sh/uv/).  **Always**
use `uv run` to invoke tools so you pick up the locked, project-pinned
versions — never call system `python`, `pytest`, `ruff`, or `ty` directly.

Install dev dependencies once per environment:

```sh
uv sync --group dev
```

Python `>=3.12` is required.

## Required checks before declaring work complete

Any change is *not* done until **all** of the following pass cleanly:

1. **Lint** — `uv run ruff check`
2. **Type check** — `uv run ty check`
3. **Tests** — `uv run pytest`

If you modified anything in `src/`, all three are mandatory.  If you
touched only docs or `AGENTS.md`/`README.md`, lint+typecheck are still
cheap and should still be run as a sanity check.

`Taskfile.yml` provides convenient shortcuts (`task lint`, `task
typecheck`, `task test`, or the combined `task check`).  These wrap the
same `uv run` invocations.

For changes that touch transport, protocol, or completion code, also
consider running the full tox matrix locally if your environment supports
it:

```sh
uv run tox            # all configured Python versions
uv run tox -e py312   # one specific version
```

CI runs `tox` on push/PR (see `.github/workflows/ci.yaml`); ruff and ty
are commented out in CI for now but **must** still be run locally.

## Coding conventions

- **Python target**: 3.12+.  Use modern syntax: `X | Y` unions,
  `from __future__ import annotations` (already in most files), PEP 695
  generics where appropriate, structural `Protocol`s for duck-typed
  interfaces (see `completion.ReadlineModule` for an example).
- **No `Any` where a Protocol or concrete type fits.**  When wrapping a
  C-extension or stub-less third-party module (e.g. `gnureadline`), define
  a `Protocol` capturing the methods you actually call and `cast()` the
  imported module to it.
- **No bare `object` returns** for things callers will index into.  If a
  function returns "some module-like thing", give it a structural type.
- **Sanitize all server-supplied strings** before printing or feeding them
  to readline.  Use `transport.sanitize` — see `docs/security-model.md`
  for the threat model.  Tool names are additionally stripped of the
  builtin `/` prefix so a malicious server cannot shadow `/quit` etc.
- **Tests live next to the module they cover** (`tests/test_<module>.py`).
  Add tests for new behaviour; do not regress coverage.
- **Do not hand-edit `uv.lock`.**  Let `uv` manage it.
- **Comments**: explain *why*, not *what*.  Don't add docstrings or type
  annotations to code you didn't change.

## Branching and commits

- Develop on a feature branch; never push directly to `main`.
- Use clear, descriptive commit messages focused on the *why*.
- Do not skip pre-commit hooks (`--no-verify`) or signing without
  explicit human request.

## Things to be careful about

- **Readline portability**: macOS ships libedit-as-readline by default,
  which differs from GNU readline in binding syntax and lacks
  `set_pre_input_hook`.  Code in `completion.py` already handles both;
  preserve those branches and the defensive `getattr` checks when
  refactoring.  See the `ReadlineModule` Protocol docstring.
- **Terminal injection**: never `print()` raw server-supplied data.
- **HTTP transport**: respect existing SSRF and redirect-handling logic
  in `transport.py`.
- **JSON-RPC IDs**: do not reuse or guess IDs; the dispatcher matches
  responses by ID.

## Quick reference

```sh
# Setup
uv sync --group dev

# The trio that gates "done"
uv run ruff check
uv run ty check
uv run pytest

# Or all at once via Task
task check

# Run the REPL itself
uv run mcp-test-driver <server-command...>
```
