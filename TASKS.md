# Stage 4 Tasks — src layout, tests, robustness

- [x] Move package to `src/` layout, update pyproject.toml
- [x] Add pytest, tox, tox-uv to dev dependencies
- [x] Configure tox for Python 3.12, 3.13, 3.14
- [x] Implement test suite (105 tests across 6 test files)
- [x] All tests pass on Python 3.12, 3.13, 3.14
- [x] Harden transport: TransportError, subprocess spawn errors, BrokenPipeError, malformed JSON, HTTP timeouts
- [x] Harden protocol: JSON-RPC error detection (McpError), response validation, protocol version warning
- [x] Harden REPL: catch TransportError/ConnectionError in main loop, validate response structure
- [x] Harden CLI: catch errors at entry points, user-friendly error messages
- [x] Add 20 robustness tests covering error paths
- [x] `ruff check`, `ty check`, tox all pass clean
