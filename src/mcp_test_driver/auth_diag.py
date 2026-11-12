# Copyright 2026 Phil Pennock — see LICENSE file.

"""OAuth discovery for MCP servers, with conformance checks.

Follows the client side of the MCP authorization spec:

  1. Protected Resource Metadata (RFC 9728): the WWW-Authenticate
     resource_metadata URL if given, else the path-inserted well-known URI,
     else the root one (2025-11-25 probe order).
  2. Authorization-server metadata: RFC 8414 and OpenID Connect discovery,
     in the spec's order for issuers with and without a path.

Every step is recorded so the user can see exactly what was tried.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .diagnostics import DiagnosticLog, Level
from .fetch import JsonFetcher, same_origin
from .messages import as_obj
from .sanitize import sanitize, sanitize_any

_AUTH_PARAM_RE = re.compile(r'\s*([!#$%&\'*+\-.^_`|~0-9A-Za-z]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^\s,]*)\s*,?')


@dataclass
class Challenge:
    scheme: str
    params: dict[str, str] = field(default_factory=dict)


def parse_www_authenticate(header: str) -> list[Challenge]:
    """Parse WWW-Authenticate into challenges (RFC 9110 §11.6.1).

    Handles several challenges in one header and quoted values with
    escapes; token68 forms are kept under the key "token68".
    """
    out: list[Challenge] = []
    pos = 0
    text = header.strip()
    while pos < len(text):
        m = re.match(r"\s*,?\s*([!#$%&'*+\-.^_`|~0-9A-Za-z]+)", text[pos:])
        if not m:
            break
        ch = Challenge(m.group(1))
        pos += m.end()
        while pos < len(text):
            pm = _AUTH_PARAM_RE.match(text, pos)
            if not pm:
                # token68, or the start of the next challenge
                t68 = re.match(r"\s+([A-Za-z0-9\-._~+/]+=*)\s*(,|$)", text[pos:])
                if t68 and not ch.params and "=" in t68.group(1):
                    ch.params["token68"] = t68.group(1)
                    pos += t68.end()
                break
            value = pm.group(2)
            if value.startswith('"'):
                value = re.sub(r"\\(.)", r"\1", value[1:-1])
            ch.params[pm.group(1).lower()] = value
            pos = pm.end()
        out.append(ch)
    return out


@dataclass
class Step:
    what: str
    url: str
    result: str


@dataclass
class AuthDiscovery:
    resource: str
    steps: list[Step] = field(default_factory=list)
    challenge: Challenge | None = None
    prm_url: str | None = None
    prm: dict[str, Any] | None = None
    issuer: str | None = None
    as_url: str | None = None
    as_metadata: dict[str, Any] | None = None

    @property
    def scopes(self) -> list[str]:
        """Scope to request, by the spec's precedence (SEP-835)."""
        if self.challenge and self.challenge.params.get("scope"):
            return self.challenge.params["scope"].split()
        supported = as_obj(self.prm).get("scopes_supported")
        if isinstance(supported, list):
            return [str(s) for s in supported]
        return []


def canonical_resource(url: str) -> str:
    """The canonical MCP server URI for RFC 8707: lower-case scheme and host, no fragment."""
    p = urlsplit(url)
    netloc = (p.hostname or "").lower()
    if p.port and p.port != {"http": 80, "https": 443}.get(p.scheme):
        netloc += f":{p.port}"
    return urlunsplit((p.scheme.lower(), netloc, p.path.rstrip("/") if p.path != "/" else "", p.query, ""))


def _origin(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "", "", ""))


def prm_candidates(mcp_url: str) -> list[str]:
    p = urlsplit(mcp_url)
    origin = _origin(mcp_url)
    path = p.path.rstrip("/")
    out = []
    if path:
        out.append(f"{origin}/.well-known/oauth-protected-resource{path}")
    out.append(f"{origin}/.well-known/oauth-protected-resource")
    return out


def as_candidates(issuer: str) -> list[str]:
    """RFC 8414 / OIDC discovery URLs, in the 2025-11-25 order."""
    p = urlsplit(issuer)
    origin = _origin(issuer)
    path = p.path.rstrip("/")
    if path:
        return [
            f"{origin}/.well-known/oauth-authorization-server{path}",
            f"{origin}/.well-known/openid-configuration{path}",
            f"{origin}{path}/.well-known/openid-configuration",
        ]
    return [f"{origin}/.well-known/oauth-authorization-server", f"{origin}/.well-known/openid-configuration"]


def discover(
    mcp_url: str,
    www_authenticate: str | None,
    log: DiagnosticLog,
    fetcher: JsonFetcher,
    *,
    protocol_version: str | None = None,
) -> AuthDiscovery:
    disc = AuthDiscovery(resource=canonical_resource(mcp_url))
    headers = {"MCP-Protocol-Version": protocol_version} if protocol_version else {}
    challenges = parse_www_authenticate(www_authenticate or "")
    bearer = next((c for c in challenges if c.scheme.lower() == "bearer"), None)
    disc.challenge = bearer
    if www_authenticate and bearer is None:
        log.violation("auth-no-bearer-challenge", "WWW-Authenticate offers no Bearer challenge",
                      "basic/authorization", level=Level.WARNING)

    candidates: list[tuple[str, str]] = []
    if bearer and bearer.params.get("resource_metadata"):
        candidates.append(("PRM (WWW-Authenticate)", bearer.params["resource_metadata"]))
    elif www_authenticate is not None:
        log.note("auth-no-resource-metadata",
                 "401 without resource_metadata; probing well-known URIs (allowed since 2025-11-25)")
    candidates += [("PRM (well-known)", u) for u in prm_candidates(mcp_url)]
    for what, url in candidates:
        got = fetcher.get(url, headers)
        disc.steps.append(Step(what, url, got.describe()))
        if got.ok and isinstance(got.data, dict):
            disc.prm_url, disc.prm = url, got.data
            break
    if disc.prm is None:
        log.violation("auth-no-prm", "no Protected Resource Metadata found (RFC 9728, required since 2025-06-18)",
                      "basic/authorization#authorization-server-discovery")
        _legacy_fallback(disc, mcp_url, log, fetcher, headers)
        return disc
    _check_prm(disc, mcp_url, log)

    servers = disc.prm.get("authorization_servers")
    if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
        return disc
    disc.issuer = servers[0]
    for url in as_candidates(disc.issuer):
        got = fetcher.get(url, headers)
        disc.steps.append(Step("AS metadata", url, got.describe()))
        if got.ok and isinstance(got.data, dict):
            disc.as_url, disc.as_metadata = url, got.data
            break
    if disc.as_metadata is None:
        log.violation("auth-no-as-metadata",
                      f"no RFC 8414 or OIDC metadata for issuer {sanitize(disc.issuer)}",
                      "basic/authorization#authorization-server-metadata-discovery")
        return disc
    check_as_metadata(disc.as_metadata, disc.issuer, log)
    return disc


def _legacy_fallback(disc: AuthDiscovery, mcp_url: str, log: DiagnosticLog, fetcher: JsonFetcher,
                     headers: dict[str, str]) -> None:
    """2025-03-26: AS metadata at the MCP server's origin, else default endpoints."""
    origin = _origin(mcp_url)
    url = f"{origin}/.well-known/oauth-authorization-server"
    got = fetcher.get(url, headers)
    disc.steps.append(Step("AS metadata (2025-03-26 style)", url, got.describe()))
    if got.ok and isinstance(got.data, dict):
        disc.issuer, disc.as_url, disc.as_metadata = origin, url, got.data
        check_as_metadata(got.data, origin, log)
    else:
        disc.issuer = origin
        disc.as_metadata = {
            "issuer": origin,
            "authorization_endpoint": f"{origin}/authorize",
            "token_endpoint": f"{origin}/token",
            "registration_endpoint": f"{origin}/register",
        }
        disc.steps.append(Step("default endpoints (2025-03-26 fallback)", f"{origin}/authorize", "assumed"))


def _check_prm(disc: AuthDiscovery, mcp_url: str, log: DiagnosticLog) -> None:
    prm = as_obj(disc.prm)
    servers = prm.get("authorization_servers")
    if not isinstance(servers, list) or not servers:
        log.violation("prm-no-authorization-servers", "PRM lacks authorization_servers", "RFC 9728 §2")
    resource = prm.get("resource")
    if not isinstance(resource, str):
        log.violation("prm-no-resource", "PRM lacks resource", "RFC 9728 §2")
    elif canonical_resource(resource) != disc.resource:
        log.violation(
            "prm-resource-mismatch",
            f"PRM resource {sanitize(resource)!r} does not match the MCP URL {sanitize(disc.resource)!r}",
            "RFC 9728 §3.3",
            level=Level.WARNING,
        )
    if disc.prm_url and not same_origin(disc.prm_url, mcp_url):
        log.note("prm-cross-origin", f"PRM is served from another origin: {sanitize(disc.prm_url)}")


def check_as_metadata(meta: dict[str, Any], issuer: str, log: DiagnosticLog) -> None:
    got_issuer = meta.get("issuer")
    if got_issuer != issuer and str(got_issuer).rstrip("/") != issuer.rstrip("/"):
        log.violation("as-issuer-mismatch",
                      f"AS metadata issuer {sanitize_any(got_issuer)!r} differs from {sanitize(issuer)!r}",
                      "RFC 8414 §3.3")
    for key in ("authorization_endpoint", "token_endpoint"):
        if not isinstance(meta.get(key), str):
            log.violation(f"as-no-{key.replace('_', '-')}", f"AS metadata lacks {key}", "RFC 8414 §2")
    methods = meta.get("code_challenge_methods_supported")
    if not isinstance(methods, list):
        log.violation("as-no-pkce-methods",
                      "AS metadata lacks code_challenge_methods_supported; clients MUST refuse to proceed",
                      "basic/authorization#authorization-code-protection")
    elif "S256" not in methods:
        log.violation("as-no-s256", "AS does not support PKCE S256", "basic/authorization")
    if not meta.get("client_id_metadata_document_supported") and not meta.get("registration_endpoint"):
        log.note("as-no-client-registration",
                 "AS offers neither Client ID Metadata Documents nor dynamic registration; "
                 "a pre-registered client is needed")


def describe(disc: AuthDiscovery) -> list[str]:
    """Human-readable lines for the discovery chain (sanitized)."""
    lines = []
    if disc.challenge:
        params = ", ".join(f"{k}={sanitize(v)!r}" for k, v in disc.challenge.params.items())
        lines.append(f"challenge: {sanitize(disc.challenge.scheme)} {params}")
    for s in disc.steps:
        lines.append(f"{s.what}: {sanitize(s.url)} → {s.result}")
    if disc.prm:
        lines.append(f"resource: {sanitize_any(disc.prm.get('resource', '?'))}")
        lines.append(f"authorization servers: {sanitize_any(disc.prm.get('authorization_servers', []))}")
    if disc.as_metadata:
        m = disc.as_metadata
        for key in ("issuer", "authorization_endpoint", "token_endpoint", "registration_endpoint",
                    "device_authorization_endpoint", "code_challenge_methods_supported",
                    "client_id_metadata_document_supported", "grant_types_supported",
                    "token_endpoint_auth_methods_supported", "authorization_response_iss_parameter_supported"):
            if key in m:
                lines.append(f"{key}: {sanitize_any(m[key])}")
    if disc.scopes:
        lines.append(f"scope to request: {' '.join(sanitize(s) for s in disc.scopes)}")
    return lines
