# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for OAuth discovery diagnostics, the JSON fetcher and server cards."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

import pytest

from mcp_test_driver.auth_diag import (
    as_candidates,
    canonical_resource,
    describe,
    discover,
    parse_www_authenticate,
    prm_candidates,
)
from mcp_test_driver.diagnostics import DiagnosticLog
from mcp_test_driver.fetch import JsonFetcher, is_loopback, same_origin, url_allowed
from tests.conftest import SAMPLE_TOOLS


def _codes(log: DiagnosticLog) -> set[str]:
    return {f.code for f in log}


def _auth_scenario(origin: str, **over: Any) -> dict[str, Any]:
    prm = {"resource": f"{origin}/mcp", "authorization_servers": [origin], "scopes_supported": ["mcp:read"]}
    asm = {
        "issuer": origin,
        "authorization_endpoint": f"{origin}/authorize",
        "token_endpoint": f"{origin}/token",
        "code_challenge_methods_supported": ["S256"],
        "client_id_metadata_document_supported": True,
    }
    docs = {
        "/.well-known/oauth-protected-resource/mcp": over.pop("prm", prm),
        "/.well-known/oauth-authorization-server": over.pop("asm", asm),
    }
    return {"tools": SAMPLE_TOOLS, "require_bearer": "good", "documents": docs, **over}


class TestParsing:
    def test_bearer_with_params(self) -> None:
        [c] = parse_www_authenticate('Bearer resource_metadata="https://x/prm", scope="a b", error="invalid_token"')
        assert c.scheme == "Bearer"
        assert c.params == {"resource_metadata": "https://x/prm", "scope": "a b", "error": "invalid_token"}

    def test_multiple_challenges_and_escapes(self) -> None:
        cs = parse_www_authenticate('Basic realm="r", Bearer realm="a \\"q\\" b", scope=x')
        assert [c.scheme for c in cs] == ["Basic", "Bearer"]
        assert cs[1].params["realm"] == 'a "q" b' and cs[1].params["scope"] == "x"

    def test_empty(self) -> None:
        assert parse_www_authenticate("") == []


class TestUrls:
    def test_prm_candidates(self) -> None:
        assert prm_candidates("https://h.example/tenant/mcp") == [
            "https://h.example/.well-known/oauth-protected-resource/tenant/mcp",
            "https://h.example/.well-known/oauth-protected-resource",
        ]
        assert prm_candidates("https://h.example") == ["https://h.example/.well-known/oauth-protected-resource"]

    def test_as_candidates_spec_order(self) -> None:
        assert as_candidates("https://a.example/tenant1") == [
            "https://a.example/.well-known/oauth-authorization-server/tenant1",
            "https://a.example/.well-known/openid-configuration/tenant1",
            "https://a.example/tenant1/.well-known/openid-configuration",
        ]
        assert as_candidates("https://a.example") == [
            "https://a.example/.well-known/oauth-authorization-server",
            "https://a.example/.well-known/openid-configuration",
        ]

    def test_canonical_resource(self) -> None:
        assert canonical_resource("HTTPS://Example.COM:443/mcp/#x") == "https://example.com/mcp"
        assert canonical_resource("http://h:8080/") == "http://h:8080"

    def test_fetch_policy(self) -> None:
        assert url_allowed("https://x.example/a") is None
        assert url_allowed("http://127.0.0.1:5/a") is None
        assert url_allowed("http://localhost/a") is None
        assert url_allowed("http://x.example/a") is not None
        assert url_allowed("file:///etc/passwd") is not None
        assert is_loopback("[::1]") and not is_loopback("10.0.0.1")
        assert same_origin("https://a/x", "https://a:443/y") and not same_origin("https://a/x", "http://a/x")


class TestDiscover:
    def test_header_driven_chain(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({})
        origin = srv.url.removesuffix("/mcp")
        srv.scenario.update(_auth_scenario(origin))
        log = DiagnosticLog()
        header = f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource/mcp", scope="mcp:write"'
        disc = discover(srv.url, header, log, JsonFetcher())
        assert disc.prm is not None and disc.as_metadata is not None
        assert disc.scopes == ["mcp:write"]  # the challenge beats scopes_supported
        assert not _codes(log)
        assert any("token_endpoint" in line for line in describe(disc))

    def test_well_known_probe_without_header_param(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({})
        origin = srv.url.removesuffix("/mcp")
        srv.scenario.update(_auth_scenario(origin))
        log = DiagnosticLog()
        disc = discover(srv.url, "Bearer", log, JsonFetcher())
        assert disc.prm_url and disc.prm_url.endswith("/oauth-protected-resource/mcp")
        assert disc.scopes == ["mcp:read"]
        assert "auth-no-resource-metadata" in _codes(log)

    def test_bad_metadata_findings(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({})
        origin = srv.url.removesuffix("/mcp")
        srv.scenario.update(_auth_scenario(
            origin,
            prm={"resource": "https://elsewhere.example/mcp", "authorization_servers": [origin]},
            asm={"issuer": "https://wrong.example", "authorization_endpoint": "x"},
        ))
        log = DiagnosticLog()
        discover(srv.url, None, log, JsonFetcher())
        assert {"prm-resource-mismatch", "as-issuer-mismatch", "as-no-token-endpoint", "as-no-pkce-methods"} <= _codes(log)

    def test_no_prm_falls_back_to_2025_03_26_defaults(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({})
        log = DiagnosticLog()
        disc = discover(srv.url, None, log, JsonFetcher())
        assert "auth-no-prm" in _codes(log)
        assert disc.as_metadata and disc.as_metadata["token_endpoint"].endswith("/token")


def test_cli_reports_auth_chain_on_401(fake_http) -> None:  # noqa: ANN001
    srv = fake_http({})
    origin = srv.url.removesuffix("/mcp")
    srv.scenario.update(_auth_scenario(origin, www_authenticate="Bearer"))
    r = subprocess.run([sys.executable, "-m", "mcp_test_driver", "--no-trace", srv.url],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 1
    assert "requires authorization" in r.stderr
    assert "oauth-protected-resource/mcp" in r.stderr and "token_endpoint" in r.stderr


def test_cli_with_bearer_token(fake_http) -> None:  # noqa: ANN001
    srv = fake_http({**_auth_scenario("http://unused"), "documents": {}})
    r = subprocess.run([sys.executable, "-m", "mcp_test_driver", "--no-trace", "--bearer-env", "TOKEN", srv.url],
                       input="echo text=ok\n", capture_output=True, text=True, timeout=30,
                       env={**os.environ, "TOKEN": "good"})
    assert r.returncode == 0, r.stderr
    assert '"text": "ok"' in r.stdout


def test_server_card_compared(fake_http, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    from mcp_test_driver.negotiate import ConnectOptions, connect
    from mcp_test_driver.protocol import McpSession
    from mcp_test_driver.repl import Repl
    from mcp_test_driver.transport import HttpTransport

    card = {"name": "other-name", "version": "1.0", "supportedProtocolVersions": ["2025-06-18"]}
    srv = fake_http({"tools": SAMPLE_TOOLS, "documents": {"/mcp/server-card": card}})
    t = HttpTransport(srv.url, timeout=10)
    t.trace = False
    s = McpSession(t)
    connect(s, ConnectOptions(era="legacy"))
    Repl(s).handle_line("/server-card")
    out = capsys.readouterr().out
    assert "other-name" in out and "AI catalog" in out
    assert {"server-card-mismatch", "server-card-versions"} <= {f.code for f in s.findings}
    t.close()
