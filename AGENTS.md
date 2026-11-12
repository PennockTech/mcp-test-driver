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
  cli.py         # argparse entry point; non-interactive modes (--check, --exec, ...)
  repl.py        # the interactive loop, list cache, task following
  commands/      # builtin /commands: registry (__init__) + one module per area
  completion.py  # readline tab-completion + help keybindings + ReadlineModule Protocol
  helptext.py    # /help, per-command and per-tool help output
  protocol.py    # McpSession: receive loop, per-revision framing, MRTR, dispatch
  negotiate.py   # era (handshake vs stateless) and revision; legacy SSE fallback
  versions.py    # registry of spec revisions: methods, capabilities, features
  messages.py    # JSON-RPC message shapes and classification
  transport/     # stdio, Streamable HTTP, legacy HTTP+SSE, SSE parser, x-mcp-header
  handlers.py    # client-side handlers for server requests (roots/list, ...)
  interactive.py # elicitation and sampling handlers (interactive and automatic)
  tasks.py       # tasks: 2025-11-25 core and the 2026 extension
  checks.py      # passive conformance checks -> findings
  probe.py       # active probes and the /check suite
  diagnostics.py # Finding / DiagnosticLog / print_findings
  report.py      # start-up connection report (/info)
  render.py      # rendering results and content blocks (sanitized)
  sanitize.py    # sanitize() / safe_json() for all server-supplied text
  fetch.py       # narrow JSON fetcher for server-supplied URLs (SSRF limits)
  auth_diag.py   # OAuth discovery (PRM, AS metadata) and checks
  oauth.py       # OAuth client: PKCE, CIMD/DCR, refresh, step-up, token cache
  parse.py       # key=value argument parsing for the REPL
  color.py       # ANSI color helpers
tests/           # pytest suite, mirrors src/ module-for-module
  fakes/         # scenario-driven fake MCP servers (stdio, HTTP, OAuth, in-memory)
  integration/   # opt-in tests against real SDK servers (MCPTD_INTEGRATION=1)
docs/            # security model, MCP spec notes, feature matrix, roadmap
  specs/         # vendored upstream MCP schemas + conformance lists (do not edit)
scripts/         # fetch-specs.sh refreshes docs/specs/; conformance-client.sh
site/            # GitHub Pages: OAuth client metadata document (CIMD)
.github/workflows/  # CI (tox + ruff + ty), conformance client suite, Pages
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

CI runs `tox`, `ruff check` and `ty check` on push/PR (see
`.github/workflows/ci.yaml`), and the official conformance suite's client
scenarios on PRs touching `src/` (`task conformance` locally; needs Node).

Tests use scenario-driven fake servers in `tests/fakes/` (see the
docstrings of `engine.py`, `http_server.py` and `auth_server.py` for the
scenario keys); extend those rather than mocking transports.

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
  to readline.  Use `sanitize.sanitize` / `sanitize.safe_json` — see `docs/security-model.md`
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

- **`pyproject.toml` `[tool.uv] exclude-newer = "3 days"` is correct.**
  Modern `uv` accepts a duration string here and resolves it to a static
  timestamp written into `uv.lock` on every run.  Older `uv` (and older
  `ruff`/`ty` that share its TOML parser) will emit a warning like
  *"failed to parse year in date '3 days'"* — that warning means **your
  tooling is stale**, not that the config is wrong.  Do not "fix" this by
  rewriting it as a date.  Upgrade your local `uv`/`ruff`/`ty` instead
  (or just ignore the warning; it does not block lint, type-check, or
  tests).  If you ran `uv sync` with stale tooling and `uv.lock` got
  rewritten, restore it: `git checkout -- uv.lock`.
- **Readline portability**: macOS ships libedit-as-readline by default,
  which differs from GNU readline in binding syntax and lacks
  `set_pre_input_hook`.  Code in `completion.py` already handles both;
  preserve those branches and the defensive `getattr` checks when
  refactoring.  See the `ReadlineModule` Protocol docstring.
- **Terminal injection**: never `print()` raw server-supplied data.
- **MCP spec revisions**: the protocol has a handshake era (2024-11-05
  to 2025-11-25) and a stateless era (2026-07-28 onwards).  Check
  `docs/mcp-feature-matrix.md` and the vendored schemas in `docs/specs/`
  before assuming a method or field exists.  Do not hand-edit
  `docs/specs/`; re-run `scripts/fetch-specs.sh`.  The planned work is
  in `docs/roadmap-multi-version.md`.
- **HTTP transport**: respect existing SSRF and redirect-handling logic
  in `transport.py`.
- **JSON-RPC IDs**: do not reuse or guess IDs; the dispatcher matches
  responses by ID.
- **Never gate features on the negotiated revision.**  The revision picks
  defaults (wire format, headers) and labels findings; users must always
  be able to send anything.  Refuse only where continuing would leak
  credentials (OAuth audience/issuer mismatches).
- **Printing happens on the main thread.**  Reader threads (stdio stderr,
  SSE streams) only queue; keep it that way so output never corrupts the
  readline prompt.

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
