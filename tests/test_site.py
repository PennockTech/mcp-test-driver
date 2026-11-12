"""Checks on the GitHub Pages site, chiefly the OAuth client metadata document."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from mcp_test_driver import oauth

SITE = Path(__file__).resolve().parent.parent / "site"
CLIENT_JSON = SITE / "oauth" / "client.json"


def _doc() -> dict:
    return json.loads(CLIENT_JSON.read_text())


def test_client_id_matches_code_and_url_rules() -> None:
    doc = _doc()
    assert doc["client_id"] == oauth.CIMD_CLIENT_ID
    parts = urlsplit(doc["client_id"])
    assert parts.scheme == "https"
    assert parts.path not in ("", "/")
    assert not parts.username and not parts.fragment and not parts.query
    assert ".." not in parts.path.split("/")
    # The client_id URL must serve this very file from the published site root.
    assert parts.path.endswith("/oauth/client.json")


def test_required_fields_present() -> None:
    doc = _doc()
    for key in ("client_id", "client_name", "redirect_uris"):
        assert doc.get(key)


def test_no_shared_secrets() -> None:
    doc = _doc()
    assert "client_secret" not in doc
    assert "client_secret_expires_at" not in doc
    forbidden = {"client_secret_post", "client_secret_basic", "client_secret_jwt"}
    assert doc.get("token_endpoint_auth_method") not in forbidden
    assert not forbidden & set(doc.get("token_endpoint_auth_methods_supported", []))


def test_redirect_uris_cover_what_we_bind() -> None:
    uris = set(_doc()["redirect_uris"])
    path = oauth.CALLBACK_PATH
    assert f"http://127.0.0.1{path}" in uris
    assert f"http://[::1]{path}" in uris
    assert f"http://127.0.0.1:{oauth.DEFAULT_CALLBACK_PORT}{path}" in uris
    for uri in uris:
        host = urlsplit(uri).hostname
        assert host in ("127.0.0.1", "::1"), uri
