# Copyright 2026 Phil Pennock — see LICENSE file.

"""OAuth client for MCP servers that require authorization.

Implements the client side of the MCP authorization spec (2025-06-18 and
later, with 2025-03-26 fallbacks):

  - discovery: see auth_diag.py
  - client registration, in the spec's priority order: pre-registered
    (--client-id), Client ID Metadata Document (our published document, or
    --client-metadata-url), Dynamic Client Registration (deprecated in
    2026-07-28 but widely deployed)
  - flows: authorization code + PKCE (S256) with a loopback redirect
    (RFC 8252) or a pasted redirect URL; client credentials; device
    authorization (RFC 8628, not MCP-specified, offered when the AS has it)
  - RFC 8707 resource indicators on every authorization and token request
  - RFC 9207 iss validation, refresh tokens, step-up on insufficient_scope
  - credentials keyed by issuer (2026-07-28), optionally cached on disk

Diagnostics come first: every deviation is a finding.  The flow refuses
to continue only where continuing would leak credentials (a resource or
issuer mismatch).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import select
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlsplit

from .auth_diag import AuthDiscovery, canonical_resource, describe, discover, parse_www_authenticate
from .color import bold, cyan, dim, eprint, red, yellow
from .diagnostics import DiagnosticLog, Level
from .fetch import Fetched, JsonFetcher
from .messages import as_obj
from .sanitize import sanitize, sanitize_any
from .transport.base import TransportError

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

LOGIN_TIMEOUT = 300.0
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
CLIENT_NAME = "mcp-test-driver"

Flow = Literal["code", "client-credentials", "device"]


class OAuthError(TransportError):
    """Login failed; a TransportError so callers report it like any HTTP failure."""


@dataclass
class OAuthOptions:
    flow: Flow = "code"
    client_id: str | None = None
    client_secret: str | None = None
    client_metadata_url: str | None = None
    callback_port: int | None = None
    browser: bool = False
    token_cache: Path | None = None
    scopes: list[str] | None = None


@dataclass
class Registration:
    client_id: str
    client_secret: str | None = None
    # How we got it: pre-registered, cimd, or dcr.
    via: str = "pre-registered"
    auth_method: str = "none"


@dataclass
class TokenSet:
    access_token: str
    token_type: str = "Bearer"
    refresh_token: str | None = None
    expires_at: float | None = None
    scope: str | None = None
    issuer: str | None = None
    resource: str | None = None

    def header(self) -> str:
        # RFC 6750 says "Bearer"; some servers echo the type in lower case.
        return f"Bearer {self.access_token}"

    def expired(self, skew: float = 30.0) -> bool:
        return self.expires_at is not None and time.time() > self.expires_at - skew


@dataclass
class _Callback:
    params: dict[str, str] = field(default_factory=dict)
    done: threading.Event = field(default_factory=threading.Event)


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _basic(client_id: str, secret: str) -> str:
    # RFC 6749 §2.3.1: form-urlencode both parts before base64.
    from urllib.parse import quote

    raw = f"{quote(client_id, safe='')}:{quote(secret, safe='')}".encode()
    return "Basic " + base64.b64encode(raw).decode()


# ── loopback redirect ──────────────────────────────────────────────────


def _make_callback_server(port: int | None) -> tuple[HTTPServer, _Callback]:
    cb = _Callback()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def do_GET(self) -> None:  # noqa: N802
            parts = urlsplit(self.path)
            if parts.path != CALLBACK_PATH or cb.done.is_set():
                self.send_response(404)
                self.end_headers()
                return
            cb.params = {k: v[0] for k, v in parse_qs(parts.query).items()}
            body = b"<html><body><p>mcp-test-driver: you can close this window.</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            cb.done.set()

    ports = [port] if port is not None else [DEFAULT_CALLBACK_PORT, 0]
    last: OSError | None = None
    for p in ports:
        try:
            server = HTTPServer(("127.0.0.1", p), Handler)
            return server, cb
        except OSError as e:
            last = e
    raise OAuthError(f"cannot listen for the OAuth redirect on 127.0.0.1: {last}")


# ── the client ─────────────────────────────────────────────────────────


class OAuthClient:
    """Obtains and refreshes tokens for one MCP server."""

    def __init__(
        self,
        mcp_url: str,
        options: OAuthOptions,
        log: DiagnosticLog,
        *,
        fetcher: JsonFetcher | None = None,
        open_url: Callable[[str], None] | None = None,
        protocol_version: str | None = None,
    ) -> None:
        self.mcp_url = mcp_url
        self.resource = canonical_resource(mcp_url)
        self.options = options
        self.log = log
        self.fetcher = fetcher or JsonFetcher()
        self.open_url = open_url
        self.protocol_version = protocol_version
        self.discovery: AuthDiscovery | None = None
        self.registration: Registration | None = None
        self.tokens: TokenSet | None = None
        self.scopes: list[str] = []
        # The issuer our registration belongs to: re-register if it changes.
        self.registration_issuer: str | None = None
        self._cached_token_endpoint: str | None = None
        self._load_cache()

    # ── public entry points ────────────────────────────────────────────

    def handle_challenge(self, status: int, www_authenticate: str | None, attempt: int) -> str | None:
        """New Authorization header after a 401/403, or None to give up."""
        challenges = parse_www_authenticate(www_authenticate or "")
        bearer = next((c for c in challenges if c.scheme.lower() == "bearer"), None)
        error = bearer.params.get("error") if bearer else None
        if status == 403 and error != "insufficient_scope":
            return None
        if attempt > 2:
            self.log.violation("auth-retry-limit", "server kept rejecting fresh credentials; giving up",
                               "basic/authorization#step-up-authorization-flow", level=Level.WARNING)
            return None
        if status == 403:
            wanted = (bearer.params.get("scope") if bearer else "") or ""
            eprint(yellow(f"Server needs more scope ({sanitize(wanted) or 'unspecified'}); re-authorizing."))
            self.scopes = sorted(set(self.scopes) | set(wanted.split()))
            return self.login(www_authenticate, step_up=True).header()
        if self.tokens and self.tokens.refresh_token and attempt == 1:
            try:
                return self.refresh().header()
            except OAuthError as e:
                eprint(dim(f"  refresh failed ({sanitize(str(e))}); logging in again"))
        return self.login(www_authenticate).header()

    def current_header(self) -> str | None:
        if self.tokens is None:
            return None
        if self.tokens.expired() and self.tokens.refresh_token:
            try:
                self.refresh()
            except OAuthError:
                return None
        return self.tokens.header()

    def login(self, www_authenticate: str | None = None, *, step_up: bool = False) -> TokenSet:
        disc = discover(self.mcp_url, www_authenticate, self.log, self.fetcher,
                        protocol_version=self.protocol_version)
        self.discovery = disc
        eprint(bold("OAuth discovery"))
        for line in describe(disc):
            eprint(dim(f"  {line}"))
        self._refuse_unsafe(disc)
        meta = as_obj(disc.as_metadata)
        if not meta:
            raise OAuthError("no authorization server metadata; cannot log in")
        if not step_up:
            self.scopes = self.options.scopes or disc.scopes
        scopes = list(self.scopes)
        if self.options.flow != "client-credentials" and "offline_access" in (meta.get("scopes_supported") or []) \
                and "offline_access" not in scopes:
            scopes.append("offline_access")
        if self.registration is None or self.registration_issuer != disc.issuer:
            self.registration = self._register(disc)
            self.registration_issuer = disc.issuer
        if self.options.flow == "client-credentials":
            tokens = self._client_credentials(meta, scopes)
        elif self.options.flow == "device":
            tokens = self._device(meta, scopes)
        else:
            tokens = self._authorization_code(disc, meta, scopes)
        tokens.issuer, tokens.resource = disc.issuer, self.resource
        self.tokens = tokens
        self._save_cache()
        eprint(bold("Logged in.") + dim(f"  scope: {sanitize(tokens.scope or ' '.join(scopes) or '(none)')}"))
        return tokens

    def refresh(self) -> TokenSet:
        if not (self.tokens and self.tokens.refresh_token and self.registration):
            raise OAuthError("no refresh token")
        meta = as_obj(self.discovery.as_metadata) if self.discovery else {}
        endpoint = meta.get("token_endpoint") or self._cached_token_endpoint
        if not endpoint:
            raise OAuthError("no token endpoint known; log in again")
        fields = {"grant_type": "refresh_token", "refresh_token": self.tokens.refresh_token,
                  "resource": self.resource}
        new = self._token_request(str(endpoint), fields)
        if new.refresh_token is None:
            new.refresh_token = self.tokens.refresh_token
        new.issuer, new.resource = self.tokens.issuer, self.resource
        self.tokens = new
        self._save_cache()
        eprint(dim("  access token refreshed"))
        return new

    def logout(self) -> None:
        self.tokens = None
        self._save_cache()

    def status_lines(self) -> list[str]:
        lines = []
        if self.registration:
            lines.append(f"client: {sanitize(self.registration.client_id)} (via {self.registration.via}, "
                         f"token auth {self.registration.auth_method})")
        if self.tokens is None:
            lines.append("not logged in")
            return lines
        t = self.tokens
        lines.append(f"issuer: {sanitize_any(t.issuer)}")
        lines.append(f"resource: {sanitize_any(t.resource)}")
        lines.append(f"scope: {sanitize_any(t.scope or '(unspecified)')}")
        if t.expires_at:
            left = int(t.expires_at - time.time())
            lines.append(f"expires: in {left}s" if left > 0 else "expires: expired")
        lines.append(f"refresh token: {'yes' if t.refresh_token else 'no'}")
        lines.append(f"access token: {t.access_token[:4]}… ({len(t.access_token)} chars)")
        return lines

    # ── safety checks ──────────────────────────────────────────────────

    def _refuse_unsafe(self, disc: AuthDiscovery) -> None:
        prm = as_obj(disc.prm)
        resource = prm.get("resource")
        if isinstance(resource, str) and canonical_resource(resource) != self.resource:
            raise OAuthError(
                f"refusing to log in: protected resource metadata names {sanitize(resource)!r}, "
                f"not this server ({sanitize(self.resource)!r}); a token for it could be misused (RFC 9728 §3.3)"
            )

    # ── registration ───────────────────────────────────────────────────

    def _register(self, disc: AuthDiscovery) -> Registration:
        meta = as_obj(disc.as_metadata)
        methods = meta.get("token_endpoint_auth_methods_supported")
        supported = methods if isinstance(methods, list) else ["client_secret_basic"]
        opts = self.options
        if opts.client_id:
            auth = "none"
            if opts.client_secret:
                auth = "client_secret_basic" if "client_secret_basic" in supported else "client_secret_post"
            eprint(dim(f"  client: pre-registered {sanitize(opts.client_id)}"))
            return Registration(opts.client_id, opts.client_secret, "pre-registered", auth)
        if opts.flow == "client-credentials":
            raise OAuthError("the client-credentials flow needs --client-id and --client-secret-env")
        if meta.get("client_id_metadata_document_supported") or opts.client_metadata_url:
            cid = opts.client_metadata_url or CIMD_CLIENT_ID
            if not meta.get("client_id_metadata_document_supported"):
                self.log.note("cimd-not-advertised", "using a Client ID Metadata Document the AS does not advertise")
            eprint(dim(f"  client: metadata document {sanitize(cid)}"))
            return Registration(cid, None, "cimd", "none")
        endpoint = meta.get("registration_endpoint")
        if isinstance(endpoint, str):
            self.log.deprecated("dcr-used", "using Dynamic Client Registration, deprecated in 2026-07-28")
            return self._dcr(endpoint)
        raise OAuthError("no way to register: pass --client-id (the AS offers neither CIMD nor registration)")

    def _dcr(self, endpoint: str) -> Registration:
        body = {
            "client_name": CLIENT_NAME,
            "redirect_uris": [f"http://127.0.0.1:{DEFAULT_CALLBACK_PORT}{CALLBACK_PATH}",
                              f"http://127.0.0.1{CALLBACK_PATH}"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "application_type": "native",
        }
        got = self.fetcher.post_json(endpoint, body)
        data = as_obj(got.data)
        if got.status not in (200, 201) or not isinstance(data.get("client_id"), str):
            raise OAuthError(f"dynamic registration failed: {self._oauth_error(got)}")
        eprint(dim(f"  client: registered dynamically as {sanitize(data['client_id'])}"))
        secret = data.get("client_secret")
        auth = str(data.get("token_endpoint_auth_method") or ("client_secret_basic" if secret else "none"))
        return Registration(data["client_id"], secret if isinstance(secret, str) else None, "dcr", auth)

    # ── flows ──────────────────────────────────────────────────────────

    def _authorization_code(self, disc: AuthDiscovery, meta: dict[str, Any], scopes: list[str]) -> TokenSet:
        assert self.registration is not None
        endpoint = meta.get("authorization_endpoint")
        if not isinstance(endpoint, str):
            raise OAuthError("AS metadata has no authorization_endpoint")
        server, cb = _make_callback_server(self.options.callback_port)
        port = server.server_address[1]
        redirect_uri = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        query = {
            "response_type": "code",
            "client_id": self.registration.client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "resource": self.resource,
        }
        if scopes:
            query["scope"] = " ".join(scopes)
        sep = "&" if "?" in endpoint else "?"
        url = f"{endpoint}{sep}{urlencode(query)}"
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        thread.start()
        try:
            eprint(cyan("Open this URL to authorize mcp-test-driver:"))
            eprint(f"  {sanitize(url)}")
            eprint(dim(f"  (listening on {redirect_uri}; or paste the final redirect URL here)"))
            if self.open_url is not None:
                self.open_url(url)
            elif self.options.browser:
                webbrowser.open(url)
            params = self._await_redirect(cb)
        finally:
            server.shutdown()
            server.server_close()
        self._check_redirect(params, state, disc, meta)
        fields = {
            "grant_type": "authorization_code",
            "code": params["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "resource": self.resource,
        }
        token_endpoint = meta.get("token_endpoint")
        if not isinstance(token_endpoint, str):
            raise OAuthError("AS metadata has no token_endpoint")
        self._cached_token_endpoint = token_endpoint
        return self._token_request(token_endpoint, fields)

    def _await_redirect(self, cb: _Callback) -> dict[str, str]:
        """Wait for the loopback redirect, or a URL pasted on a terminal."""
        deadline = time.monotonic() + LOGIN_TIMEOUT
        interactive = sys.stdin.isatty()
        while time.monotonic() < deadline:
            if cb.done.wait(0.25):
                return cb.params
            if interactive:
                ready, _, _ = select.select([sys.stdin], [], [], 0)
                if ready:
                    line = sys.stdin.readline().strip()
                    if line:
                        return {k: v[0] for k, v in parse_qs(urlsplit(line).query).items()}
        raise OAuthError("timed out waiting for the authorization redirect")

    def _check_redirect(self, params: dict[str, str], state: str, disc: AuthDiscovery, meta: dict[str, Any]) -> None:
        if "error" in params:
            raise OAuthError(f"authorization failed: {sanitize(params['error'])} "
                             f"{sanitize(params.get('error_description', ''))}".strip())
        if params.get("state") != state:
            raise OAuthError("authorization response state does not match; refusing it (possible CSRF)")
        iss = params.get("iss")
        advertised = meta.get("authorization_response_iss_parameter_supported") is True
        if iss is None and advertised:
            raise OAuthError("AS advertises iss in responses but sent none; refusing (RFC 9207)")
        if iss is not None and iss != disc.issuer and iss.rstrip("/") != str(disc.issuer).rstrip("/"):
            raise OAuthError(f"iss {sanitize(iss)!r} is not the expected issuer; refusing (RFC 9207, mix-up attack)")
        if iss is not None and not advertised:
            self.log.note("iss-not-advertised", "AS sent iss without advertising authorization_response_iss_parameter_supported")
        if "code" not in params:
            raise OAuthError("authorization response has no code")

    def _client_credentials(self, meta: dict[str, Any], scopes: list[str]) -> TokenSet:
        endpoint = meta.get("token_endpoint")
        if not isinstance(endpoint, str):
            raise OAuthError("AS metadata has no token_endpoint")
        grants = meta.get("grant_types_supported")
        if isinstance(grants, list) and "client_credentials" not in grants:
            self.log.note("cc-not-advertised", "AS does not list client_credentials in grant_types_supported")
        self._cached_token_endpoint = endpoint
        fields = {"grant_type": "client_credentials", "resource": self.resource}
        if scopes:
            fields["scope"] = " ".join(scopes)
        return self._token_request(endpoint, fields)

    def _device(self, meta: dict[str, Any], scopes: list[str]) -> TokenSet:
        assert self.registration is not None
        endpoint = meta.get("device_authorization_endpoint")
        token_endpoint = meta.get("token_endpoint")
        if not isinstance(endpoint, str) or not isinstance(token_endpoint, str):
            raise OAuthError("AS offers no device authorization endpoint (RFC 8628)")
        self.log.note("device-grant", "using the device authorization grant (RFC 8628), which MCP does not specify")
        fields = {"client_id": self.registration.client_id, "resource": self.resource}
        if scopes:
            fields["scope"] = " ".join(scopes)
        got = self.fetcher.post_form(endpoint, fields)
        data = as_obj(got.data)
        if got.status != 200 or "device_code" not in data:
            raise OAuthError(f"device authorization failed: {self._oauth_error(got)}")
        uri = data.get("verification_uri_complete") or data.get("verification_uri")
        eprint(cyan(f"Visit {sanitize_any(uri)} and enter code {bold(sanitize_any(data.get('user_code', '')))}"))
        if self.open_url is not None and isinstance(uri, str):
            self.open_url(uri)
        interval = float(data.get("interval", 5))
        deadline = time.monotonic() + float(data.get("expires_in", LOGIN_TIMEOUT))
        self._cached_token_endpoint = token_endpoint
        while time.monotonic() < deadline:
            time.sleep(interval)
            try:
                return self._token_request(token_endpoint, {"grant_type": DEVICE_GRANT, "device_code": data["device_code"],
                                                            "resource": self.resource})
            except _Pending as p:
                if p.slow_down:
                    interval += 5
        raise OAuthError("device code expired")

    # ── token endpoint ─────────────────────────────────────────────────

    def _token_request(self, endpoint: str, fields: dict[str, str]) -> TokenSet:
        assert self.registration is not None
        reg = self.registration
        headers: dict[str, str] = {}
        if reg.auth_method == "client_secret_basic" and reg.client_secret:
            headers["Authorization"] = _basic(reg.client_id, reg.client_secret)
        else:
            fields = {**fields, "client_id": reg.client_id}
            if reg.auth_method == "client_secret_post" and reg.client_secret:
                fields["client_secret"] = reg.client_secret
        got = self.fetcher.post_form(endpoint, fields, headers)
        data = as_obj(got.data)
        if got.status != 200 or not isinstance(data.get("access_token"), str):
            err = data.get("error")
            if err in ("authorization_pending", "slow_down"):
                raise _Pending(err == "slow_down")
            raise OAuthError(f"token request failed: {self._oauth_error(got)}")
        if str(data.get("token_type", "")).lower() != "bearer":
            self.log.violation("token-type", f"token_type is {sanitize_any(data.get('token_type'))!r}, not Bearer",
                               "RFC 6750", level=Level.WARNING)
        expires = data.get("expires_in")
        return TokenSet(
            access_token=data["access_token"],
            token_type=str(data.get("token_type", "Bearer")),
            refresh_token=data.get("refresh_token") if isinstance(data.get("refresh_token"), str) else None,
            expires_at=time.time() + float(expires) if isinstance(expires, (int, float)) else None,
            scope=data.get("scope") if isinstance(data.get("scope"), str) else None,
        )

    @staticmethod
    def _oauth_error(got: Fetched) -> str:
        data = as_obj(got.data)
        if data.get("error"):
            return sanitize(f"{data['error']} {data.get('error_description', '')}".strip())
        return got.describe()

    # ── cache (credentials keyed by issuer + resource) ─────────────────

    def _cache_key(self) -> str:
        return self.resource

    def _load_cache(self) -> None:
        path = self.options.token_cache
        if path is None or not path.exists():
            return
        mode = path.stat().st_mode & 0o077
        if mode:
            eprint(red(f"Warning: token cache {path} is readable by others (mode {oct(mode)}); not using it."))
            return
        try:
            entry = json.loads(path.read_text()).get(self._cache_key())
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(entry, dict):
            return
        reg, tok = entry.get("registration"), entry.get("tokens")
        if isinstance(reg, dict):
            self.registration = Registration(**reg)
            self.registration_issuer = entry.get("issuer")
        if isinstance(tok, dict):
            self.tokens = TokenSet(**tok)
        self._cached_token_endpoint = entry.get("token_endpoint")

    def _save_cache(self) -> None:
        path = self.options.token_cache
        if path is None:
            return
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        data[self._cache_key()] = {
            "issuer": self.registration_issuer,
            "token_endpoint": self._cached_token_endpoint,
            "registration": asdict(self.registration) if self.registration else None,
            "tokens": asdict(self.tokens) if self.tokens else None,
        }
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)


class _Pending(Exception):
    def __init__(self, slow_down: bool) -> None:
        self.slow_down = slow_down
