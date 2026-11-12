# Copyright 2026 Phil Pennock — see LICENSE file.

"""OAuth login flows against the fake authorization server."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any

import pytest
import urllib3

from mcp_test_driver.negotiate import ConnectOptions, connect
from mcp_test_driver.oauth import CIMD_CLIENT_ID, OAuthClient, OAuthError, OAuthOptions, pkce_pair
from mcp_test_driver.protocol import McpSession
from mcp_test_driver.repl import Repl
from mcp_test_driver.transport import AuthRequiredError, HttpTransport
from tests.conftest import SAMPLE_TOOLS


def browser(url: str) -> None:
    """Play the user's browser: follow the authorize redirect to the loopback callback."""
    pool = urllib3.PoolManager()
    r = pool.request("GET", url, redirect=False)
    loc = r.headers.get("Location")
    if loc:
        pool.request("GET", loc)


def _session(srv, **opts: Any) -> tuple[McpSession, OAuthClient]:  # noqa: ANN001
    t = HttpTransport(srv.url, timeout=10)
    t.trace = False
    s = McpSession(t)
    client = OAuthClient(srv.url, OAuthOptions(callback_port=0, **opts), s.findings, open_url=browser)
    t.auth = client
    return s, client


def _log(srv, kind: str) -> list[dict[str, Any]]:  # noqa: ANN001
    return [d for k, d in srv.auth.log if k == kind]


def _codes(s: McpSession) -> set[str]:
    return {f.code for f in s.findings}


SCEN: dict[str, Any] = {"tools": SAMPLE_TOOLS, "versions": ["2025-11-25"]}


class TestAuthorizationCode:
    def test_cimd_login_on_first_401(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {"cimd": True}})
        s, client = _session(srv)
        connect(s, ConnectOptions(era="legacy"))
        assert s.state.initialized and client.tokens is not None
        [auth] = _log(srv, "authorize")
        assert auth["client_id"] == CIMD_CLIENT_ID
        assert auth["code_challenge_method"] == "S256"
        assert auth["resource"] == srv.url
        assert auth["scope"] == "mcp"  # from scopes_supported
        [tok] = _log(srv, "token")
        assert tok["resource"] == srv.url and tok["code_verifier"] and tok["_auth_method"] == "none"
        assert [t["name"] for t in s.list_tools()] == ["echo"]
        s.transport.close()

    def test_challenge_scope_wins(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {"cimd": True, "challenge_scope": "mcp:special"}})
        s, _ = _session(srv)
        connect(s, ConnectOptions(era="legacy"))
        assert _log(srv, "authorize")[0]["scope"] == "mcp:special"
        s.transport.close()

    def test_offline_access_added_when_supported(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {"cimd": True, "scopes_supported": ["mcp", "offline_access"]}})
        s, _ = _session(srv)
        connect(s, ConnectOptions(era="legacy"))
        assert _log(srv, "authorize")[0]["scope"].split() == ["mcp", "offline_access"]
        s.transport.close()

    def test_dynamic_registration(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {"registration": True}})
        s, client = _session(srv)
        connect(s, ConnectOptions(era="legacy"))
        [reg] = _log(srv, "register")
        assert reg["application_type"] == "native" and reg["token_endpoint_auth_method"] == "none"
        assert client.registration and client.registration.via == "dcr"
        assert "dcr-used" in _codes(s)
        s.transport.close()

    @pytest.mark.parametrize("methods,expected", [
        (["client_secret_basic", "client_secret_post"], "client_secret_basic"),
        (["client_secret_post"], "client_secret_post"),
    ])
    def test_pre_registered_confidential(self, fake_http, methods: list[str], expected: str) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {"clients": {"c1": "s:1&"}, "auth_methods": methods}})
        s, _ = _session(srv, client_id="c1", client_secret="s:1&")
        connect(s, ConnectOptions(era="legacy"))
        assert _log(srv, "token")[0]["_auth_method"] == expected
        s.transport.close()

    def test_step_up_on_insufficient_scope(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {"cimd": True, "tool_scopes": {"echo": "write"}}})
        s, _ = _session(srv)
        connect(s, ConnectOptions(era="legacy"))
        resp = s.call_tool("echo", {"text": "hi"})
        assert "result" in resp
        scopes = [a["scope"].split() for a in _log(srv, "authorize")]
        assert scopes == [["mcp"], ["mcp", "write"]]
        s.transport.close()

    def test_refresh_when_expired(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {"cimd": True}})
        s, client = _session(srv)
        connect(s, ConnectOptions(era="legacy"))
        assert client.tokens is not None
        client.tokens.expires_at = time.time() - 1
        s.list_tools()
        grants = [t["grant_type"] for t in _log(srv, "token")]
        assert grants == ["authorization_code", "refresh_token"]
        s.transport.close()


class TestRefusals:
    @pytest.mark.parametrize("cfg,match", [
        ({"iss": "wrong"}, "not the expected issuer"),
        ({"iss_advertised": True, "iss": "missing"}, "sent none"),
        ({"deny": True}, "access_denied"),
        ({"prm_override": {"resource": "https://elsewhere.example/mcp"}}, "refusing to log in"),
    ])
    def test_refused(self, fake_http, cfg: dict[str, Any], match: str) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {"cimd": True, **cfg}})
        s, _ = _session(srv)
        with pytest.raises(OAuthError, match=match):
            connect(s, ConnectOptions(era="legacy"))
        s.transport.close()

    def test_iss_correct_and_advertised(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {"cimd": True, "iss_advertised": True}})
        s, _ = _session(srv)
        connect(s, ConnectOptions(era="legacy"))
        assert s.state.initialized
        s.transport.close()

    def test_no_registration_route(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {}})
        s, _ = _session(srv)
        with pytest.raises(OAuthError, match="--client-id"):
            connect(s, ConnectOptions(era="legacy"))
        s.transport.close()

    def test_without_oauth_401_surfaces(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {"cimd": True}})
        t = HttpTransport(srv.url, timeout=10)
        t.trace = False
        with pytest.raises(AuthRequiredError):
            connect(McpSession(t), ConnectOptions(era="legacy"))


class TestOtherFlows:
    def test_client_credentials(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {"clients": {"m2m": "sec"}}})
        s, _ = _session(srv, flow="client-credentials", client_id="m2m", client_secret="sec")
        connect(s, ConnectOptions(era="legacy"))
        [tok] = _log(srv, "token")
        assert tok["grant_type"] == "client_credentials" and tok["resource"] == srv.url
        assert not _log(srv, "authorize")
        s.transport.close()

    def test_device_grant(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({**SCEN, "oauth": {"device": True}})
        s, _ = _session(srv, flow="device", client_id="public-cli")
        connect(s, ConnectOptions(era="legacy"))
        assert [t["grant_type"] for t in _log(srv, "token")][-1].endswith("device_code")
        assert "device-grant" in _codes(s)
        s.transport.close()


class TestCache:
    def test_tokens_cached_and_reused(self, fake_http, tmp_path: Path) -> None:  # noqa: ANN001
        cache = tmp_path / "tokens.json"
        srv = fake_http({**SCEN, "oauth": {"cimd": True}})
        s, _ = _session(srv, token_cache=cache)
        connect(s, ConnectOptions(era="legacy"))
        assert stat.S_IMODE(cache.stat().st_mode) == 0o600
        assert srv.url in json.loads(cache.read_text())
        s.transport.close()
        s2, client2 = _session(srv, token_cache=cache)
        assert client2.tokens is not None
        connect(s2, ConnectOptions(era="legacy"))
        assert len(_log(srv, "authorize")) == 1  # no second login
        s2.transport.close()

    def test_world_readable_cache_ignored(self, fake_http, tmp_path: Path) -> None:  # noqa: ANN001
        cache = tmp_path / "tokens.json"
        cache.write_text("{}")
        os.chmod(cache, 0o644)
        srv = fake_http({**SCEN, "oauth": {"cimd": True}})
        _, client = _session(srv, token_cache=cache)
        assert client.tokens is None


def test_repl_auth_commands(fake_http, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    srv = fake_http({**SCEN, "oauth": {"cimd": True}})
    s, _ = _session(srv)
    connect(s, ConnectOptions(era="legacy"))
    r = Repl(s)
    r.handle_line("/auth status")
    out = capsys.readouterr().out
    assert "refresh token: yes" in out and "tok-" in out and "client:" in out
    r.handle_line("/auth refresh")
    r.handle_line("/auth logout")
    assert "Logged out" in capsys.readouterr().out
    s.transport.close()


def test_pkce_pair() -> None:
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128 and "=" not in challenge


def test_cli_client_credentials(fake_http) -> None:  # noqa: ANN001
    import subprocess
    import sys

    srv = fake_http({**SCEN, "oauth": {"clients": {"m2m": "zq-s3cr3t-77"}}})
    r = subprocess.run(
        [sys.executable, "-m", "mcp_test_driver", "--auth-flow", "client-credentials",
         "--client-id", "m2m", "--client-secret-env", "M2M_SECRET", srv.url],
        input="echo text=authed\n", capture_output=True, text=True, timeout=30,
        env={**os.environ, "M2M_SECRET": "zq-s3cr3t-77"},
    )
    assert r.returncode == 0, r.stderr
    assert '"text": "authed"' in r.stdout
    assert "Logged in." in r.stderr
    assert "zq-s3cr3t-77" not in r.stdout + r.stderr  # never echoed, even with tracing
