# Stage 4 Complete

## Summary

- Moved package to `src/mcp_test_driver/` layout
- 105 tests passing on Python 3.12, 3.13, 3.14 via tox
- Comprehensive robustness hardening across all modules

## Robustness changes

### transport.py
- New `TransportError` exception for transport-level failures
- `StdioTransport._launch()`: catches FileNotFoundError, PermissionError, OSError
- `StdioTransport._send()`: catches BrokenPipeError, OSError
- `StdioTransport._recv()`: catches malformed JSON, returns None with warning
- `StdioTransport.close()`: catches OSError on stdin.close()
- `HttpTransport._post()`: catches urllib3 connection errors, adds timeout
- `HttpTransport._post()`: catches malformed JSON responses, uses try/finally for release_conn

### protocol.py
- New `McpError` exception for JSON-RPC error responses
- `_check_error()`: detects and raises on error objects
- `initialize()`: validates result structure, warns on protocol version mismatch
- `list_tools()`: validates response structure, returns empty list on malformed data

### repl.py
- Main loop catches `TransportError`, `ConnectionError`, and generic exceptions
- `_print_result()`: handles JSON-RPC error responses, validates structure at every level
- Tool invocation catches transport errors without crashing

### cli.py
- Entry points catch `TransportError`, `McpError`, `ConnectionError`
- User-friendly error messages to stderr, clean exit codes
- KeyboardInterrupt handled at top level (exit 130)
