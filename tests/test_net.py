# Copyright 2026 Phil Pennock — see LICENSE file.

"""Proxy selection, TLS trust, and HTTP through a (fake) proxy."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from mcp_test_driver.fetch import JsonFetcher
from mcp_test_driver.negotiate import ConnectOptions, connect
from mcp_test_driver.net import NetConfig, redact_url
from mcp_test_driver.protocol import McpSession
from mcp_test_driver.transport import HttpTransport, TransportError
from tests.conftest import PROXIED_HOST, SAMPLE_TOOLS, CertFiles

PROXY_VARS = ("https_proxy", "http_proxy", "all_proxy", "no_proxy",
              "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY")

# This sandbox's own no_proxy, as a real-world example of the forms in use.
SANDBOX_NO_PROXY = (
    "localhost,127.0.0.1,::1,127.0.0.0/8,0.0.0.0/8,::,169.254.0.0/16,api.anthropic.com,"
    "registry.npmjs.org,pypi.org,files.pythonhosted.org,host.docker.internal,10.0.0.0/8,"
    "172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,.svc.cluster.local,*.svc.cluster.local"
)


def _route(env: dict[str, str], url: str, **kw: Any) -> str | None:
    return NetConfig.from_env(env, **kw).proxy_for(url).proxy


class TestProxySelection:
    def test_per_scheme_and_fallback(self) -> None:
        env = {"HTTPS_PROXY": "http://s:1", "HTTP_PROXY": "http://p:2", "ALL_PROXY": "http://a:3"}
        assert _route(env, "https://example.com/mcp") == "http://s:1"
        assert _route(env, "http://example.com/mcp") == "http://p:2"
        assert _route({"ALL_PROXY": "http://a:3"}, "https://example.com/") == "http://a:3"
        assert _route({}, "https://example.com/") is None

    def test_lowercase_wins_and_bare_host_port(self) -> None:
        env = {"https_proxy": "low:8080", "HTTPS_PROXY": "http://UP:1"}
        assert _route(env, "https://example.com/") == "http://low:8080"

    def test_httpoxy(self) -> None:
        # Under CGI, HTTP_PROXY may come from a client's Proxy header.
        env = {"HTTP_PROXY": "http://evil:1", "REQUEST_METHOD": "GET"}
        assert _route(env, "http://example.com/") is None
        assert _route({**env, "http_proxy": "http://ok:1"}, "http://example.com/") == "http://ok:1"

    def test_loopback_never_proxied(self) -> None:
        env = {"ALL_PROXY": "http://p:1"}
        for url in ("http://127.0.0.1:5/mcp", "https://localhost/x", "http://[::1]:9/", "http://a.localhost/",
                    "http://127.1.2.3/"):
            assert _route(env, url) is None, url
        assert _route(env, "http://127.0.0.1/", proxy="http://p:1") is None

    def test_explicit_proxy_and_direct(self) -> None:
        env = {"HTTPS_PROXY": "http://env:1", "NO_PROXY": "internal.example"}
        assert _route(env, "https://example.com/", proxy="http://cli:2") == "http://cli:2"
        assert _route(env, "https://internal.example/", proxy="http://cli:2") is None  # NO_PROXY still applies
        assert _route(env, "https://example.com/", proxy="direct") is None

    @pytest.mark.parametrize("url,direct", [
        ("https://pypi.org/simple", True),
        ("https://files.pythonhosted.org/x", True),
        ("https://notpypi.org/", False),
        ("https://foo.svc.cluster.local/", True),
        ("https://10.1.2.3:8443/", True),
        ("https://192.168.7.7/", True),
        ("https://8.8.8.8/", False),
        ("https://[::]/", True),
        ("https://example.com/", False),
    ])
    def test_sandbox_no_proxy(self, url: str, direct: bool) -> None:
        env = {"HTTPS_PROXY": "http://p:1", "no_proxy": SANDBOX_NO_PROXY}
        assert (_route(env, url) is None) is direct

    @pytest.mark.parametrize("entry,url,direct", [
        ("*", "https://anything.example/", True),
        ("example.com", "https://api.example.com/", True),
        ("example.com", "https://notexample.com/", False),
        (".example.com", "https://example.com/", True),
        ("*.example.com", "https://a.b.example.com/", True),
        ("example.com:8443", "https://example.com:8443/", True),
        ("example.com:8443", "https://example.com/", False),
        ("10.0.0.0/8", "https://10.9.9.9/", True),
        ("10.0.0.0/8", "https://ten.example/", False),  # names are not resolved
        ("[2001:db8::1]:443", "https://[2001:db8::1]/", True),
        ("2001:db8::/32", "https://[2001:db8::5]:8080/", True),
        ("Example.COM", "https://EXAMPLE.com/", True),
    ])
    def test_no_proxy_forms(self, entry: str, url: str, direct: bool) -> None:
        assert (_route({"HTTPS_PROXY": "http://p:1", "NO_PROXY": entry}, url) is None) is direct

    @pytest.mark.parametrize("proxy,match", [
        ("socks5://p:1080", "SOCKS proxies are not supported"),
        ("ftp://p:21", "unsupported proxy scheme"),
        ("http://p:notaport", "bad port"),
        ("http://", "no host"),
    ])
    def test_bad_explicit_proxy(self, proxy: str, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            NetConfig.from_env({}, proxy=proxy)

    def test_bad_env_proxy_fails_requests_but_not_startup(self) -> None:
        net = NetConfig.from_env({"ALL_PROXY": "socks5h://p:1080"})  # stdio users must not be broken
        route = net.proxy_for("https://example.com/")
        assert route.proxy is None and route.error and "SOCKS" in route.error
        assert any("unusable" in line for line in net.describe("https://example.com/"))
        t = HttpTransport("https://example.com/mcp", net=net, timeout=5)
        t.trace = False
        with pytest.raises(TransportError, match="SOCKS"):
            t.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    def test_credentials_redacted(self) -> None:
        net = NetConfig.from_env({}, proxy="http://alice:s3cr3t@proxy.example:3128")
        text = "\n".join(net.describe("https://example.com/"))
        assert "alice:***@proxy.example:3128" in text and "s3cr3t" not in text
        assert redact_url("http://u@h/") == "http://u@h/"


class TestTrust:
    def test_missing_bundle(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            NetConfig.from_env({}, ca_bundle=str(tmp_path / "nope.pem"))

    def test_unparseable_bundle(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.pem"
        bad.write_text("not a certificate\n")
        with pytest.raises(ValueError, match="cannot load CA bundle"):
            NetConfig.from_env({}, ca_bundle=str(bad))

    def test_bundle_from_env_var(self, test_certs: CertFiles) -> None:
        net = NetConfig.from_env({"MCP_TEST_DRIVER_CA_BUNDLE": str(test_certs.ca)})
        assert net.ca_bundle == str(test_certs.ca)

    def test_bundle_is_added_to_system_trust(self, test_certs: CertFiles) -> None:
        base = NetConfig.from_env({}).ssl_context().get_ca_certs()
        extra = NetConfig.from_env({}, ca_bundle=str(test_certs.ca)).ssl_context().get_ca_certs()
        assert len(extra) == len(base) + 1
        assert any(("commonName", "mcp-test-driver test CA") in rdn for c in extra for rdn in c["subject"])


# ── end to end, through FakeProxy ──────────────────────────────────────


def _connect(url: str, net: NetConfig, era: str = "legacy") -> McpSession:
    t = HttpTransport(url, net=net, timeout=10)
    t.trace = False
    s = McpSession(t)
    connect(s, ConnectOptions(era=era, probe_timeout=2))  # type: ignore[arg-type]
    return s


def _tls(certs: CertFiles) -> dict[str, str]:
    return {"certfile": str(certs.cert), "keyfile": str(certs.key)}


class TestThroughProxy:
    @pytest.mark.parametrize("http_mode", ["json", "sse"])
    def test_plain_http_target(self, fake_http, fake_proxy, http_mode: str) -> None:  # noqa: ANN001
        srv = fake_http({"tools": SAMPLE_TOOLS, "host": PROXIED_HOST, "http_mode": http_mode})
        proxy = fake_proxy()
        s = _connect(srv.url, NetConfig.from_env({"HTTP_PROXY": proxy.url}))
        assert s.state.initialized
        assert "via proxy" in str(s.call_tool("echo", {"text": "via proxy"})["result"])
        assert all(t.startswith(f"http://{PROXIED_HOST}:") for t in proxy.targets())
        assert len(proxy.targets()) >= 3  # initialize, initialized, tools/call
        assert "via proxy" in s.transport.describe()
        s.transport.close()

    def test_https_target_via_connect(self, fake_http, fake_proxy, test_certs: CertFiles) -> None:  # noqa: ANN001
        srv = fake_http({"tools": SAMPLE_TOOLS, "host": PROXIED_HOST, "tls": _tls(test_certs)})
        proxy = fake_proxy()
        s = _connect(srv.url, NetConfig.from_env({"HTTPS_PROXY": proxy.url}, ca_bundle=str(test_certs.ca)))
        assert s.list_tools()[0]["name"] == "echo"
        assert {m for m, _, _ in proxy.log} == {"CONNECT"}
        assert proxy.targets()[0] == f"{PROXIED_HOST}:{srv.url.rsplit(':', 1)[1].split('/')[0]}"
        s.transport.close()

    def test_https_untrusted_names_the_fix(self, fake_http, fake_proxy, test_certs: CertFiles) -> None:  # noqa: ANN001
        srv = fake_http({"tools": SAMPLE_TOOLS, "host": PROXIED_HOST, "tls": _tls(test_certs)})
        proxy = fake_proxy()
        with pytest.raises(TransportError, match="--ca-bundle") as exc:
            _connect(srv.url, NetConfig.from_env({"HTTPS_PROXY": proxy.url}))
        assert "via proxy" in str(exc.value)

    @pytest.mark.filterwarnings("ignore::urllib3.exceptions.InsecureRequestWarning")
    def test_https_insecure(self, fake_http, fake_proxy, test_certs: CertFiles) -> None:  # noqa: ANN001
        srv = fake_http({"tools": SAMPLE_TOOLS, "host": PROXIED_HOST, "tls": _tls(test_certs)})
        proxy = fake_proxy()
        s = _connect(srv.url, NetConfig.from_env({"HTTPS_PROXY": proxy.url}, insecure=True))
        assert s.state.initialized
        s.transport.close()

    def test_proxy_basic_auth_from_url(self, fake_http, fake_proxy) -> None:  # noqa: ANN001
        srv = fake_http({"tools": SAMPLE_TOOLS, "host": PROXIED_HOST})
        proxy = fake_proxy(require_auth=("alice", "p@ss:w"))
        port = proxy.url.rsplit(":", 1)[1]
        net = NetConfig.from_env({}, proxy=f"http://alice:p%40ss%3Aw@127.0.0.1:{port}")
        s = _connect(srv.url, net)
        assert s.state.initialized
        assert all(auth and auth.startswith("Basic ") for _, _, auth in proxy.log)
        assert "alice:***@" in s.transport.describe() and "p%40ss" not in s.transport.describe()
        s.transport.close()

    def test_proxy_auth_refused(self, fake_http, fake_proxy) -> None:  # noqa: ANN001
        srv = fake_http({"tools": SAMPLE_TOOLS, "host": PROXIED_HOST})
        proxy = fake_proxy(require_auth=("alice", "pw"))
        with pytest.raises(TransportError):
            _connect(srv.url, NetConfig.from_env({}, proxy=proxy.url))

    def test_legacy_sse_fallback_keeps_settings(self, fake_http, fake_proxy) -> None:  # noqa: ANN001
        srv = fake_http({"tools": SAMPLE_TOOLS, "host": PROXIED_HOST, "legacy_sse": True})
        proxy = fake_proxy()
        net = NetConfig.from_env({"HTTP_PROXY": proxy.url})
        s = _connect(srv.url, net)
        assert s.transport.kind == "sse" and getattr(s.transport, "net") is net
        assert any("/messages" in t for t in proxy.targets())
        s.transport.close()

    def test_metadata_fetch(self, fake_http, fake_proxy, test_certs: CertFiles) -> None:  # noqa: ANN001
        srv = fake_http({"host": PROXIED_HOST, "tls": _tls(test_certs), "documents": {"/card.json": {"ok": 1}}})
        proxy = fake_proxy()
        net = NetConfig.from_env({"HTTPS_PROXY": proxy.url}, ca_bundle=str(test_certs.ca))
        got = JsonFetcher(net=net).get(f"{srv.origin}/card.json")
        assert got.data == {"ok": 1} and proxy.log
        bad = JsonFetcher(net=NetConfig.from_env({"HTTPS_PROXY": proxy.url})).get(f"{srv.origin}/card.json")
        assert bad.error and "--ca-bundle" in bad.error

    def test_direct_https_with_bundle(self, fake_http, test_certs: CertFiles) -> None:  # noqa: ANN001
        srv = fake_http({"tools": SAMPLE_TOOLS, "tls": _tls(test_certs)})
        s = _connect(srv.url, NetConfig.from_env({}, ca_bundle=str(test_certs.ca)))
        assert s.state.initialized
        s.transport.close()


def _env_without_proxies(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in PROXY_VARS and k != "MCP_TEST_DRIVER_CA_BUNDLE"}
    return {**env, **extra}


class TestCli:
    def test_env_proxy_and_bundle(self, fake_http, fake_proxy, test_certs: CertFiles) -> None:  # noqa: ANN001
        srv = fake_http({"tools": SAMPLE_TOOLS, "host": PROXIED_HOST, "tls": _tls(test_certs)})
        proxy = fake_proxy()
        r = subprocess.run(
            [sys.executable, "-m", "mcp_test_driver", "--no-trace", "--era", "legacy", "-c", "echo text=tunnelled",
             srv.url],
            capture_output=True, text=True, timeout=60,
            env=_env_without_proxies(HTTPS_PROXY=proxy.url, MCP_TEST_DRIVER_CA_BUNDLE=str(test_certs.ca)),
        )
        assert r.returncode == 0, r.stderr
        assert "tunnelled" in r.stdout
        out = r.stdout + r.stderr
        assert f"proxy: {proxy.url} (from HTTPS_PROXY)" in out
        assert f"CA bundle {test_certs.ca}" in out

    def test_flags_and_redaction(self, fake_http, fake_proxy) -> None:  # noqa: ANN001
        srv = fake_http({"tools": SAMPLE_TOOLS, "host": PROXIED_HOST})
        proxy = fake_proxy(require_auth=("bob", "zq-pr0xy-77"))
        port = proxy.url.rsplit(":", 1)[1]
        r = subprocess.run(
            [sys.executable, "-m", "mcp_test_driver", "--era", "legacy", "--proxy",
             f"http://bob:zq-pr0xy-77@127.0.0.1:{port}", "-c", "echo text=hi", srv.url],
            capture_output=True, text=True, timeout=60, env=_env_without_proxies(),
        )
        assert r.returncode == 0, r.stderr
        assert "zq-pr0xy-77" not in r.stdout + r.stderr  # even with tracing on
        assert "bob:***@" in r.stdout + r.stderr

    def test_bad_settings_exit_early(self, tmp_path: Path) -> None:
        for args in (["--proxy", "socks5://p:1"], ["--ca-bundle", str(tmp_path / "missing.pem")]):
            r = subprocess.run([sys.executable, "-m", "mcp_test_driver", *args, "https://example.com/mcp"],
                               capture_output=True, text=True, timeout=30, env=_env_without_proxies())
            assert r.returncode != 0 and "Error:" in r.stderr, args
