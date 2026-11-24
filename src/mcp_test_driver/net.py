# Copyright 2026 Phil Pennock — see LICENSE file.

"""Outbound HTTP: proxy selection and TLS trust, in one place.

Every HTTP client the driver uses is an `HttpClient` built from a
`NetConfig`, so the MCP transports, metadata fetches and OAuth all follow
the same proxy and certificate settings.

Proxies come from --proxy or the usual environment variables
(https_proxy, http_proxy, all_proxy, no_proxy, in either case).  Loopback
targets are never proxied: local test servers and the OAuth callback must
stay local, and a remote proxy could not reach them anyway.

One CA bundle (--ca-bundle) is trusted *in addition to* the system store,
for both the target and an https:// proxy.  An intercepting proxy re-signs
target certificates, so its CA belongs in the target trust anyway; a
separate proxy-only bundle would only matter for a proxy CA deliberately
not trusted for targets.
"""

from __future__ import annotations

import ipaddress
import os
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit

import urllib3
from urllib3.util.ssl_ import create_urllib3_context

CA_BUNDLE_ENV = "MCP_TEST_DRIVER_CA_BUNDLE"
DIRECT = "direct"


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def redact_url(url: str) -> str:
    """The URL with any password replaced, safe to print."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = parts.netloc.rsplit("@", 1)[1]
    user = parts.username or ""
    return parts._replace(netloc=f"{user}:***@{netloc}").geturl()


def _env(environ: Mapping[str, str], name: str) -> tuple[str, str] | None:
    """(variable name, value) for a proxy variable; lowercase wins, as in curl."""
    for var in (name.lower(), name.upper()):
        # httpoxy: under CGI, HTTP_PROXY can come from a request header.
        if var == "HTTP_PROXY" and "REQUEST_METHOD" in environ:
            continue
        value = environ.get(var, "").strip()
        if value:
            return var, value
    return None


def _check_proxy_url(url: str, origin: str) -> str:
    if "://" not in url:
        url = "http://" + url  # curl's default for a bare host:port
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme.startswith("socks"):
        raise ValueError(f"{origin}: SOCKS proxies are not supported ({redact_url(url)}); use an HTTP proxy")
    if scheme not in ("http", "https"):
        raise ValueError(f"{origin}: unsupported proxy scheme {scheme!r} in {redact_url(url)}")
    try:
        parts.port  # noqa: B018  (raises on a malformed port)
    except ValueError:
        raise ValueError(f"{origin}: bad port in proxy URL {redact_url(url)}") from None
    if not parts.hostname:
        raise ValueError(f"{origin}: proxy URL has no host: {redact_url(url)}")
    return url


@dataclass(frozen=True)
class _NoProxyRule:
    host: str | None = None  # domain suffix match
    network: ipaddress.IPv4Network | ipaddress.IPv6Network | None = None
    port: int | None = None
    everything: bool = False

    def matches(self, host: str, port: int) -> bool:
        if self.everything:
            return True
        if self.port is not None and self.port != port:
            return False
        if self.network is not None:
            try:
                return ipaddress.ip_address(host) in self.network
            except ValueError:
                return False  # hostnames are not resolved
        assert self.host is not None
        return host == self.host or host.endswith("." + self.host)


def _parse_no_proxy(value: str) -> tuple[_NoProxyRule, ...]:
    rules: list[_NoProxyRule] = []
    for raw in value.replace(",", " ").split():
        entry = raw.strip().lower()
        if entry == "*":
            rules.append(_NoProxyRule(everything=True))
            continue
        port: int | None = None
        # An IPv6 literal is bracketed when it carries a port: [::1]:8080.
        if entry.startswith("["):
            addr, _, rest = entry[1:].partition("]")
            if rest.startswith(":") and rest[1:].isdigit():
                port = int(rest[1:])
            entry = addr
        elif entry.count(":") == 1:
            name, _, p = entry.partition(":")
            if p.isdigit():
                entry, port = name, int(p)
        try:
            rules.append(_NoProxyRule(network=ipaddress.ip_network(entry, strict=False), port=port))
            continue
        except ValueError:
            pass
        entry = entry.removeprefix("*.").lstrip(".")
        if entry:
            rules.append(_NoProxyRule(host=entry, port=port))
    return tuple(rules)


@dataclass(frozen=True)
class Route:
    proxy: str | None  # None: direct
    reason: str
    error: str | None = None  # an unusable proxy setting; requests must fail, not go direct


class ProxyConfigError(urllib3.exceptions.HTTPError):
    """The proxy setting for this target is unusable (raised per request)."""


@dataclass(frozen=True)
class NetConfig:
    verify_tls: bool = True
    ca_bundle: str | None = None
    proxy: str | None = None  # explicit --proxy; overrides the environment
    use_env_proxy: bool = True
    environ: Mapping[str, str] = field(default_factory=dict, repr=False)
    no_proxy: tuple[_NoProxyRule, ...] = ()
    no_proxy_source: str | None = None

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        proxy: str | None = None,
        ca_bundle: str | None = None,
        insecure: bool = False,
    ) -> NetConfig:
        """Build and validate the settings; ValueError explains what is wrong."""
        env = dict(os.environ if environ is None else environ)
        if ca_bundle is None:
            ca_bundle = env.get(CA_BUNDLE_ENV) or None
        if ca_bundle is not None:
            ca_bundle = os.path.expanduser(ca_bundle)
            if not os.path.exists(ca_bundle):
                raise ValueError(f"CA bundle not found: {ca_bundle}")
            if not os.access(ca_bundle, os.R_OK):
                raise ValueError(f"CA bundle not readable: {ca_bundle}")
        use_env = True
        if proxy is not None and proxy.strip().lower() in (DIRECT, "none", ""):
            proxy, use_env = None, False
        elif proxy is not None:
            proxy = _check_proxy_url(proxy.strip(), "--proxy")
        found_np = _env(env, "no_proxy")
        cfg = cls(
            verify_tls=not insecure,
            ca_bundle=ca_bundle,
            proxy=proxy,
            use_env_proxy=use_env,
            environ=env,
            no_proxy=_parse_no_proxy(found_np[1]) if found_np else (),
            no_proxy_source=found_np[0] if found_np else None,
        )
        cfg.ssl_context()  # fail now on an unparseable bundle
        return cfg

    # ── TLS ────────────────────────────────────────────────────────────

    def ssl_context(self) -> ssl.SSLContext:
        ctx = create_urllib3_context()
        if not self.verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        ctx.load_default_certs()  # honours SSL_CERT_FILE / SSL_CERT_DIR
        if self.ca_bundle:
            try:
                if os.path.isdir(self.ca_bundle):
                    ctx.load_verify_locations(capath=self.ca_bundle)
                else:
                    ctx.load_verify_locations(cafile=self.ca_bundle)
            except (ssl.SSLError, OSError) as e:
                raise ValueError(f"cannot load CA bundle {self.ca_bundle}: {e}") from None
        return ctx

    # ── proxy selection ────────────────────────────────────────────────

    def proxy_for(self, url: str) -> Route:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if is_loopback(host):
            return Route(None, "loopback is never proxied")
        if self.proxy is None and not self.use_env_proxy:
            return Route(None, "--proxy direct")
        scheme = parts.scheme.lower()
        try:
            port = parts.port or {"https": 443, "http": 80}.get(scheme, 0)
        except ValueError:
            port = 0
        for rule in self.no_proxy:
            if rule.matches(host, port):
                return Route(None, f"matches {self.no_proxy_source}")
        if self.proxy is not None:
            return Route(self.proxy, "--proxy")
        if self.use_env_proxy:
            for name in (f"{scheme}_proxy", "all_proxy"):
                found = _env(self.environ, name)
                if found:
                    # Checked only when used, so a bad proxy variable does not
                    # break stdio servers or targets it doesn't apply to.
                    try:
                        return Route(_check_proxy_url(found[1], found[0]), f"from {found[0]}")
                    except ValueError as e:
                        return Route(None, f"from {found[0]}", error=str(e))
        return Route(None, "no proxy configured")

    def describe(self, url: str | None = None) -> list[str]:
        """Report lines; credentials are never shown."""
        lines: list[str] = []
        if url is not None:
            route = self.proxy_for(url)
            if route.error:
                lines.append(f"proxy: unusable ({route.error})")
            elif route.proxy:
                lines.append(f"proxy: {redact_url(route.proxy)} ({route.reason})")
            else:
                lines.append(f"proxy: none ({route.reason})")
        if not self.verify_tls:
            lines.append("TLS: certificate verification disabled (--insecure)")
        elif self.ca_bundle:
            lines.append(f"TLS: system trust plus CA bundle {self.ca_bundle}")
        return lines


def _proxy_parts(proxy: str) -> tuple[str, dict[str, str]]:
    """The proxy URL without credentials, and the headers that carry them."""
    parts = urlsplit(proxy)
    if parts.username is None:
        return proxy, {}
    netloc = parts.netloc.rsplit("@", 1)[1]
    creds = f"{unquote(parts.username)}:{unquote(parts.password or '')}"
    return parts._replace(netloc=netloc).geturl(), urllib3.make_headers(proxy_basic_auth=creds)


class HttpClient:
    """A urllib3 pool manager that routes each request by NetConfig.

    Offers the subset of PoolManager the driver uses: request() and clear().
    """

    def __init__(self, net: NetConfig | None = None, **pool_kw: Any) -> None:
        self.net = net if net is not None else NetConfig.from_env()
        self._ctx = self.net.ssl_context()
        self._kw = pool_kw
        self._direct: urllib3.PoolManager | None = None
        self._proxied: dict[str, urllib3.ProxyManager] = {}

    def route_for(self, url: str) -> Route:
        return self.net.proxy_for(url)

    def _manager(self, url: str) -> urllib3.PoolManager:
        route = self.net.proxy_for(url)
        if route.error:
            raise ProxyConfigError(route.error)
        if route.proxy is None:
            if self._direct is None:
                self._direct = urllib3.PoolManager(ssl_context=self._ctx, **self._kw)
            return self._direct
        mgr = self._proxied.get(route.proxy)
        if mgr is None:
            proxy_url, proxy_headers = _proxy_parts(route.proxy)
            mgr = urllib3.ProxyManager(
                proxy_url, proxy_headers=proxy_headers, proxy_ssl_context=self._ctx,
                ssl_context=self._ctx, **self._kw,
            )
            self._proxied[route.proxy] = mgr
        return mgr

    def request(self, method: str, url: str, **kw: Any) -> urllib3.BaseHTTPResponse:
        return self._manager(url).request(method, url, **kw)

    def clear(self) -> None:
        if self._direct is not None:
            self._direct.clear()
        for mgr in self._proxied.values():
            mgr.clear()


def describe_error(client: HttpClient, url: str, err: BaseException) -> str:
    """A transport-level failure, naming the proxy and hinting at TLS trust."""
    route = client.route_for(url)
    via = f" via proxy {redact_url(route.proxy)}" if route.proxy else ""
    if isinstance(err, ProxyConfigError):
        return str(err)
    if isinstance(err, urllib3.exceptions.ProxyError):
        return f"Proxy {redact_url(route.proxy or '?')} failed: {err}"
    if isinstance(err, urllib3.exceptions.SSLError):
        hint = ""
        if client.net.verify_tls and not client.net.ca_bundle:
            hint = " (to trust a private or intercepting-proxy CA, use --ca-bundle)"
        return f"TLS/certificate error connecting to {url}{via}: {err}{hint}"
    return f"Connection failed{via}: {err}"
