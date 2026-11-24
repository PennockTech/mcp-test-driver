# 0006: One builder for HTTP clients, one proxy policy, one CA bundle

Status: Accepted (2026-11-24)

## Context

The driver makes HTTP requests from several places: the MCP transports,
OAuth metadata and token requests, server cards.  In sandboxes and behind
corporate or debugging proxies, any one of them ignoring the proxy or CA
settings fails, or leaks around the proxy.

## Decision

- Every client is a `net.HttpClient` built from the session's
  `NetConfig`.  No other module constructs a urllib3 pool or imports
  another HTTP library.
- Proxies: `--proxy`, else `https_proxy` / `http_proxy` / `all_proxy`
  minus `no_proxy`, read as curl reads them.  Loopback targets are never
  proxied.  A setting that cannot be honoured (SOCKS, a bad URL) is an
  error, never a silent direct connection.
- Trust: one `--ca-bundle`, *added* to the system store, for both targets
  and an `https://` proxy.  An intercepting proxy re-signs target
  certificates, so its CA must be trusted for targets anyway; a
  proxy-only bundle would rarely matter.

## Consequences

- `tests/test_security.py::test_http_clients_only_built_in_net` fails on
  a client built elsewhere.
- Replacing the system store is left to OpenSSL's `SSL_CERT_FILE` /
  `SSL_CERT_DIR`.
- SOCKS proxies are unsupported.
