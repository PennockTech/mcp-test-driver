# MCP Specification Notes

Notes from the MCP specification research conducted during security hardening.
Based on the 2025-03-26 spec at https://spec.modelcontextprotocol.io/ and
related RFCs/proposals.

## Transport Mechanisms

### 1. STDIO Transport
- Local, single-client only
- Newline-delimited JSON-RPC over stdin/stdout
- No embedded newlines in messages
- Server stderr for logging (inherited by client)
- No built-in authentication (credentials from environment)

### 2. Streamable HTTP Transport (current standard, 2025-03-26)
- Replaces the deprecated HTTP+SSE transport (from 2024-11-05)
- Single HTTP endpoint supporting POST and GET
- POST: client-to-server messages (JSON-RPC in body)
- GET: server-to-client listening (optional SSE stream)
- Responses may be `application/json` or `text/event-stream`
- Session management via `Mcp-Session-Id` header
- Supports batching of JSON-RPC messages
- Standard HTTP authentication (Bearer tokens, API keys, OAuth 2.0, mTLS)

**Note**: There is no separate "SSE transport" in the current spec. The old
HTTP+SSE transport from protocol version 2024-11-05 is deprecated.

## Discovery Mechanisms

### .well-known URIs (Two Distinct Specs)

**1. OAuth Protected Resource Metadata (RFC 9728) — Mandatory**
- Endpoint: `/.well-known/oauth-protected-resource` (or sub-path variant)
- Purpose: Advertise authorization server locations
- Content: JSON with `authorization_servers` array
- Discovery: Also via `WWW-Authenticate` header in 401 responses
- Status: **Core spec, mandatory for servers requiring auth**

**2. MCP Server Discovery — Proposed**
- Endpoint: `/.well-known/mcp` or `/.well-known/mcp/server-card.json`
- Purpose: Advertise MCP server capabilities without live connection
- Content: Server name/version, available transports, capabilities, tool listings
- Proposals: SEP-1649 (server cards), SEP-1960 (manifest endpoint)
- Status: **Not yet in core spec, actively being implemented**

### DNS-Based Discovery — Proposed (SEP-1959)
- SRV records: `_mcp._tcp.example.com`
- TXT records: `_mcp.yourdomain.com` for capability advertisement
- DANE records: `_443._tcp.mcp.example.com` for certificate pinning
- Status: **Not standardized, proposal under consideration**

## Session Management

- Server MAY assign `Mcp-Session-Id` during initialize response
- ID must be globally unique, cryptographically secure
- ID must contain only visible ASCII (0x21-0x7E)
- Client MUST include the header in all subsequent requests
- Server returns 404 if session has expired → client must re-initialize
- Client can send HTTP DELETE to terminate session
- SSE events may have `id` fields for stream resumption (`Last-Event-ID`)

## TLS Requirements

- **TLS 1.2 minimum** required
- **TLS 1.3 recommended** for production
- All authorization endpoints MUST use HTTPS
- Redirect URIs (except localhost) MUST use HTTPS
- mTLS supported as authentication mechanism
- No specific cipher suite requirements in core spec

## Authentication (OAuth 2.1)

- Based on OAuth 2.1 (draft-ietf-oauth-v2-1-13)
- PKCE mandatory (S256 method preferred)
- Resource Indicators (RFC 8707) mandatory in auth/token requests
- Bearer tokens in `Authorization` header
- Refresh tokens optional (`offline_access` scope)
- Client registration: Client ID Metadata Documents, pre-registration, or
  Dynamic Client Registration (RFC 7591)

### Auth Discovery
Authorization servers must provide at least one of:
1. OAuth 2.0 Authorization Server Metadata (RFC 8414)
2. OpenID Connect Discovery 1.0

For issuer URLs with path components, clients try in order:
1. `/.well-known/oauth-authorization-server/path`
2. `/.well-known/openid-configuration/path`
3. `/path/.well-known/openid-configuration`

## HTTP Redirect Handling

- OAuth redirect URIs must be exact matches against pre-registered values
- Only `localhost` or `HTTPS` redirect URIs allowed
- DNS rebinding protection: validate `Origin` header, bind to 127.0.0.1
- No MCP-specific redirect behavior beyond OAuth 2.1 compliance

## Security Considerations

- Treat `Mcp-Session-Id` as untrusted input
- Never tie authorization to session ID alone
- Validate `Origin` headers on local servers
- SSRF protection for authorization servers fetching client metadata
- Token theft mitigation per OAuth 2.1 best practices

## Key RFCs and References

- MCP Spec: https://spec.modelcontextprotocol.io/
- MCP Transports: https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
- MCP Authorization: https://modelcontextprotocol.io/specification/draft/basic/authorization
- RFC 9728: OAuth 2.0 Protected Resource Metadata
- RFC 8414: OAuth 2.0 Authorization Server Metadata
- RFC 8707: Resource Indicators for OAuth 2.0
- RFC 7591: OAuth 2.0 Dynamic Client Registration
- OAuth 2.1: draft-ietf-oauth-v2-1-13
- SEP-1649: MCP Server Cards (proposed)
- SEP-1960: .well-known/mcp Discovery (proposed)
- SEP-1959: DNS-Based MCP Server Identity Verification (proposed)
