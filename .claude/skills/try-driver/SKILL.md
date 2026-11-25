---
name: try-driver
description: Run mcp-test-driver non-interactively against a scenario-configured fake MCP server (stdio or HTTP) to see a change working end to end. Use to verify REPL, protocol, transport or rendering changes beyond the unit tests.
---

# Try the driver against a fake server

The fakes in `tests/fakes/` behave as any MCP server you describe in a
scenario dict.  Scenario keys are documented in the docstrings of
`tests/fakes/engine.py` (all transports), `http_server.py` (HTTP extras)
and `auth_server.py` (OAuth).

Put scratch files outside the repository.

## stdio

Write a scenario, then run REPL lines with `-c` (repeatable; exit status
1 if any line failed):

```sh
cat > "${TMPDIR:-/tmp}/scenario.json" <<'JSON'
{"mode": "dual", "versions": ["2025-11-25", "2026-07-28"],
 "tools": [{"name": "echo", "description": "Echo text",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}
JSON
uv run mcp-test-driver --no-trace -c '/list' -c 'echo text=hi' -c '/findings' \
    python -I tests/fakes/stdio_server.py "${TMPDIR:-/tmp}/scenario.json"
```

Options go before the target.  Useful ones:

- `--era handshake` / `--era stateless`: force one side of a `dual`
  server.
- `--trace` (the default) shows the JSON-RPC traffic; `--no-trace` hides it.
- `-q`: skip the connection report.
- `--check --json` / `--probe --json`: one JSON document on stdout; the
  report goes to stderr.
- `--elicitation-auto`: answer form elicitations without a prompt.

A tool with a `tool_scripts` entry can send notifications, server
requests, MRTR results, task handles, stderr, or exit mid-call.

## HTTP

The HTTP fake has no command line; start it from Python and point the
driver at its URL:

```sh
uv run python - <<'PY'
import subprocess, sys
from tests.fakes.http_server import FakeHttpServer

scenario = {
    "mode": "stateless", "versions": ["2026-07-28"], "http_mode": "sse",
    "tools": [{"name": "echo", "inputSchema": {"type": "object"}}],
}
srv = FakeHttpServer(scenario).start()
try:
    subprocess.run([sys.executable, "-m", "mcp_test_driver", "--no-trace", "-q",
                    "-c", "/list", "-c", "echo text=hi", srv.url])
finally:
    srv.stop()
PY
```

Run it from the repository root so `tests.fakes` imports.

## Interactive behaviour

Tab-completion, F1 help and prompts need a terminal, and `-c` does not
exercise them.  Cover those with tests (`tests/test_completion.py`,
`tests/test_interactive.py`), or say plainly that they were not run.

## Then

If the behaviour is worth keeping, turn the scenario into a test; see
`tests/AGENTS.md`.
