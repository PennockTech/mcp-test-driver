# Security Model

This document describes the security measures implemented in mcp-test-driver
to protect against malicious MCP servers.

## Threat Model

mcp-test-driver connects to arbitrary MCP servers — both local (stdio) and
remote (HTTPS).  A malicious server may attempt:

1. **Memory exhaustion** via oversized responses
2. **SSRF** via HTTP redirects to internal services
3. **Terminal injection** via ANSI escape sequences in tool names/descriptions
4. **HTTP header injection** via crafted session IDs
5. **Client hang** via slow or incomplete responses
6. **Response confusion** via mismatched JSON-RPC IDs

## Implemented Defenses

### Size Limits (transport.py)

| Limit | Value | Applies to |
|-------|-------|------------|
| `MAX_LINE_BYTES` | 16 MiB | Stdio readline — oversized lines are drained and discarded |
| `MAX_RESPONSE_BYTES` | 16 MiB | HTTP JSON body read + SSE stream total bytes |

### Network Security (transport.py)

- **No HTTP redirects**: `urllib3.util.Retry(redirect=0)` prevents SSRF.
  3xx responses are explicitly rejected with an error message.
- **HTTP status validation**: 4xx and 5xx are rejected before body parsing.
- **Separate timeouts**: 10s connect, 60s read (via `urllib3.util.Timeout`).
- **TLS verification**: On by default (urllib3 v2 default).  Constructor
  accepts `verify_tls=False` for testing with self-signed certs.
- **TLS error messages**: SSLError distinguished from connection errors.

### Input Sanitization (transport.py, completion.py, repl.py, protocol.py)

- **`sanitize()`**: Strips ANSI escape sequences, OSC sequences, and control
  characters (0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F, 0x7F, 0x9B) from strings.
  Applied to all server-supplied data before terminal display.
- **Session ID**: Stripped to visible ASCII only (0x21-0x7E per MCP spec).
- **Tool names**: Sanitized and spaces replaced with underscores for readline
  safety.  Empty names are skipped.
- **Server name/version**: Sanitized in connection banner.

### Protocol Validation (protocol.py)

- **JSON-RPC error detection**: `_check_error()` raises `McpError` on error
  responses.
- **Response ID validation**: `_check_id()` warns when response ID doesn't
  match the expected request ID.
- **Server notification skipping**: Stdio transport skips up to 32
  notifications while awaiting a response.  Notifications are always displayed.
- **Protocol version check**: Warns if server's protocol version differs.
- **Response structure validation**: Missing/malformed fields handled gracefully.

### Error Handling (cli.py, repl.py)

- **Subprocess errors**: FileNotFoundError, PermissionError caught with
  user-friendly messages.
- **REPL resilience**: TransportError, ConnectionError caught in main loop
  so the REPL survives transient failures.
- **Clean exit**: KeyboardInterrupt → exit 130.

## What Is NOT Implemented

- **OAuth 2.1 authentication**: The spec defines full OAuth, but as a test
  driver we don't implement auth flows.
- **DNS discovery**: SRV/TXT/DANE records are proposed but not standardized.
- **.well-known metadata**: Neither OAuth resource metadata nor MCP server
  cards are fetched.
- **Stdio read timeout**: No timeout on `readline()` — requires `select()`
  or threads.  Server can still hang the client by never sending a newline.
  This is noted as a known limitation.
