# Stage 5 Tasks — Security hardening

- [x] Bounded stdio readline (MAX_LINE_BYTES = 16 MiB, drain oversized lines)
- [x] Bounded HTTP response body read (MAX_RESPONSE_BYTES = 16 MiB)
- [x] Bounded SSE stream parsing (total byte counter)
- [x] Disable HTTP redirect following (SSRF protection, urllib3 Retry(redirect=0))
- [x] Validate HTTP status codes before parsing body
- [x] Separate connect/read timeouts (10s connect, 60s read)
- [x] TLS error differentiation (SSLError vs connection error)
- [x] ANSI escape and control character sanitization (sanitize() function)
- [x] Sanitize all server-supplied strings before terminal display
- [x] Sanitize tool names for readline safety (strip spaces, control chars)
- [x] Session ID sanitization (visible ASCII only per MCP spec, 0x21-0x7E)
- [x] JSON-RPC response ID validation (_check_id warns on mismatch)
- [x] Skip server notifications in stdio request/response flow
- [x] 24 new security tests in test_security.py
- [x] 129 tests pass on Python 3.12, 3.13, 3.14
- [x] Functional tests pass against aifr (stdio) and unicode.mcp.pennock.tech (HTTPS)
