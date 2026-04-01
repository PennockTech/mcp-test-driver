# Stage 5 Complete — Security Hardening

## Threat model

Analyzed from the perspective of a malicious remote MCP server attacking
this client.  Three parallel security audits (MCP spec researcher, malicious
server attacker, HTTP/TLS specialist) identified vulnerabilities across
all modules.

## Changes

### transport.py — Core security hardening

- **Bounded reads**: `readline(MAX_LINE_BYTES)` for stdio (16 MiB);
  `resp.read(MAX_RESPONSE_BYTES + 1)` for HTTP; byte counter in SSE parser
- **No redirects**: `Retry(redirect=0)` + explicit 3xx rejection (SSRF defense)
- **HTTP status validation**: 4xx/5xx rejected before body parsing
- **Timeout granularity**: Separate connect (10s) and read (60s) timeouts
- **TLS errors**: Distinguished from connection errors in error messages
- **Session ID sanitization**: Strips non-visible-ASCII per MCP spec (0x21-0x7E)
- **Notification skip**: Stdio `request()` skips up to 32 server notifications
  while awaiting the response with matching ID
- **`sanitize()` function**: Strips ANSI escapes, OSC sequences, control chars
  from server-supplied strings before terminal display

### protocol.py — Response validation

- **`_check_id()`**: Warns when response ID doesn't match request ID
- **`_check_error()`**: Detects JSON-RPC error objects in responses
- All three methods (initialize, list_tools, call_tool) validate IDs

### completion.py — Input sanitization

- Tool names sanitized and spaces replaced with underscores for readline safety
- Empty tool names skipped
- All descriptions, argument types, enum values sanitized before display

### repl.py — Display sanitization

- Server name, tool names, descriptions all run through `sanitize()`
- JSON-RPC error responses handled with structured display

## MCP spec findings (for future work)

- **MCP Streamable HTTP** is the current transport (2025-03-26 spec);
  the old HTTP+SSE transport is deprecated
- **Two .well-known specs**: OAuth Protected Resource Metadata (RFC 9728,
  mandatory) at `/.well-known/oauth-protected-resource`, and MCP Server
  Discovery (SEP-1649/1960, proposed) at `/.well-known/mcp`
- **DNS discovery**: Proposed but not standardized (SEP-1959: SRV/TXT/DANE)
- **OAuth 2.1 auth**: Full auth flow with PKCE, resource indicators, etc.
  Not implemented (test driver doesn't need auth for most testing)
- **TLS 1.2 minimum**: Required by spec; our urllib3 default satisfies this

## Test coverage

- 129 tests across 8 test files
- `test_security.py` (24 tests): sanitization, size limits, redirect
  rejection, status validation, session ID sanitization, ID matching
- All pass on Python 3.12, 3.13, 3.14
