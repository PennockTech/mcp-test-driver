# Stage 4 Handoff

## Completed so far

- Moved package to `src/mcp_test_driver/` layout
- `pyproject.toml` updated with `module-root = "src"`, pytest/tox/tox-uv in dev deps
- tox configured for py312, py313, py314 — all 85 tests pass on all versions
- Test coverage across all modules:
  - `test_parse.py` (16 tests) — key=val parsing, JSON, coercion, edge cases
  - `test_color.py` (4 tests) — TTY vs non-TTY color output
  - `test_completion.py` (18 tests) — dot-commands, CompletionState, completer
  - `test_transport.py` (23 tests) — frame encoding, stdio mock, HTTP mock, SSE parser
  - `test_protocol.py` (10 tests) — initialize, list_tools, call_tool, reconnect
  - `test_repl.py` (8 tests) — SessionCache, _print_result
  - `test_cli.py` (6 tests) — help flags, transport detection

## Next: robustness improvements

Areas to harden:
- Error handling in transports (connection failures, timeouts, malformed JSON)
- Graceful handling of subprocess crashes mid-session
- HTTP transport: retry on transient failures, proper timeout handling
- Protocol: handle malformed server responses without crashing
- REPL: handle edge cases in user input
