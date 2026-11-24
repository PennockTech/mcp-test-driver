# 0010: The OAuth client identity is a fixed, published URL

Status: Accepted (2026-11-12)

## Context

From 2025-11-25 the preferred way to identify a client without
pre-registration is a Client ID Metadata Document: the `client_id` is an
HTTPS URL serving the client's metadata.  The URL is baked into every
released version, and authorization servers may remember it.

## Decision

- `client_id` is `https://mcp-test-driver.pennock.tech/oauth/client.json`
  (`oauth.CIMD_CLIENT_ID`), served by GitHub Pages from
  `site/oauth/client.json` on a custom domain.  Treat it as permanent.
- `redirect_uris` lists `http://127.0.0.1/callback` and
  `http://[::1]/callback` without a port, for servers that allow any
  loopback port (RFC 9700 §4.1.3), and `http://127.0.0.1:33418/callback`
  for servers that compare exactly.  The driver binds 33418 first, then
  any free port.
- The document carries no secret: `token_endpoint_auth_method` is `none`.
- `--client-metadata-url` and `--client-id` are the escape hatches.

## Consequences

- Changing the URL, or removing a redirect URI, breaks released versions
  against servers that hold the old values.  Only add.
- `tests/test_site.py` fails when the document and `oauth.py` disagree.
- `site/` changes deploy from `main` via `.github/workflows/pages.yaml`.
