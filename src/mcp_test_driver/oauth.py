# Copyright 2026 Phil Pennock — see LICENSE file.

"""OAuth client for MCP servers that require authorization."""

from __future__ import annotations

# Our Client ID Metadata Document (CIMD), published from site/oauth/client.json
# by .github/workflows/pages.yaml.  Changing this URL changes our client
# identity at every authorization server, and older releases keep using the
# old one, so treat it as stable; --client-metadata-url overrides it.
CIMD_CLIENT_ID = "https://pennocktech.github.io/mcp-test-driver/oauth/client.json"

CALLBACK_PATH = "/callback"
# A fixed port is listed in the CIMD for authorization servers that compare
# redirect URIs exactly; RFC 9700 §4.1.3 servers accept any loopback port, so
# we fall back to an ephemeral port when this one is busy.
DEFAULT_CALLBACK_PORT = 33418
