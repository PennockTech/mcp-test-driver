# Copyright 2026 Phil Pennock — see LICENSE file.

"""Builtins that fetch documents beside the MCP endpoint: auth metadata, server cards."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from ..color import bold, dim, red
from ..diagnostics import Level, print_findings
from ..fetch import JsonFetcher
from ..messages import as_obj
from ..sanitize import safe_json, sanitize, sanitize_any
from . import command

if TYPE_CHECKING:
    from ..repl import Repl


def _http_url(repl: Repl) -> str | None:
    url = getattr(repl.session.transport, "url", None)
    if not url:
        print(dim("This is an HTTP feature; the current transport is stdio."))
    return url


def auth_discover(repl: Repl) -> Any:
    from ..auth_diag import describe, discover

    url = _http_url(repl)
    if url is None:
        return None
    fetcher = JsonFetcher(verify_tls=repl.verify_tls)
    disc = discover(url, repl.session.last_www_authenticate, repl.session.findings, fetcher,
                    protocol_version=repl.session.state.version)
    print(bold("OAuth discovery"))
    for line in describe(disc):
        print(f"  {line}")
    print_findings(repl.session.findings.take_unshown())
    return disc


def _oauth_client(repl: Repl, create: bool) -> Any:
    from ..oauth import OAuthClient, OAuthOptions

    transport = repl.session.transport
    auth = getattr(transport, "auth", None)
    if auth is None and create:
        url = _http_url(repl)
        if url is None:
            return None
        auth = OAuthClient(url, OAuthOptions(), repl.session.findings,
                           fetcher=JsonFetcher(verify_tls=repl.verify_tls),
                           protocol_version=repl.session.state.version)
        setattr(transport, "auth", auth)
    return auth


@command(
    "/auth",
    group="Auth",
    summary="OAuth: discovery, login, status, refresh, logout",
    usage=(
        "/auth discover   show the protected-resource and authorization-server metadata chain\n"
        "/auth login      log in now (authorization code + PKCE unless --auth-flow chose otherwise)\n"
        "/auth status     who we are logged in as, scopes, expiry\n"
        "/auth refresh    use the refresh token\n"
        "/auth logout     forget tokens (and the cached copy)"
    ),
    words=("discover", "login", "status", "refresh", "logout"),
)
def cmd_auth(repl: Repl, rest: str) -> None:
    from ..oauth import OAuthError

    sub = rest.split()[0] if rest.split() else "status"
    try:
        if sub == "discover":
            auth_discover(repl)
        elif sub == "login":
            auth = _oauth_client(repl, create=True)
            if auth is not None:
                tokens = auth.login(repl.session.last_www_authenticate)
                setattr(repl.session.transport, "authorization", tokens.header())
        elif sub in ("status", "refresh", "logout"):
            auth = _oauth_client(repl, create=False)
            if auth is None:
                print(dim("OAuth is not in use; /auth login starts it."))
                return
            if sub == "refresh":
                setattr(repl.session.transport, "authorization", auth.refresh().header())
            elif sub == "logout":
                auth.logout()
                setattr(repl.session.transport, "authorization", None)
                print(dim("Logged out."))
                return
            for line in auth.status_lines():
                print(f"  {line}")
        else:
            print(red(f"Unknown /auth subcommand {sanitize(sub)!r}"))
    except OAuthError as e:
        print(red(f"OAuth: {sanitize(str(e))}"))
    print_findings(repl.session.findings.take_unshown())


def _card_urls(url: str) -> list[tuple[str, str]]:
    p = urlsplit(url)
    origin = urlunsplit((p.scheme, p.netloc, "", "", ""))
    return [
        ("server card", url.rstrip("/") + "/server-card"),
        ("AI catalog", origin + "/.well-known/ai-catalog.json"),
    ]


@command("/server-card", group="Diagnostics",
         summary="Fetch the Server Card and AI Catalog (SEP-2127) and compare with the live server",
         usage="/server-card [url]")
def cmd_server_card(repl: Repl, rest: str) -> None:
    url = rest.strip() or _http_url(repl)
    if not url:
        return
    fetcher = JsonFetcher(verify_tls=repl.verify_tls)
    for label, doc_url in _card_urls(url):
        got = fetcher.get(doc_url)
        print(f"{bold(label)}: {sanitize(doc_url)} → {got.describe()}")
        if got.ok:
            print(safe_json(got.data))
            if label == "server card":
                _compare_card(repl, as_obj(got.data))


def _compare_card(repl: Repl, card: dict[str, Any]) -> None:
    """Cards are static; when they disagree with the live server, the server wins."""
    log = repl.session.findings
    info = repl.session.server_info
    for key in ("name", "version"):
        if key in card and key in info and card[key] != info[key]:
            log.violation("server-card-mismatch",
                          f"server card {key} {sanitize_any(card[key])!r} differs from live "
                          f"{sanitize_any(info[key])!r}", "SEP-2127", level=Level.WARNING)
    versions = card.get("supportedProtocolVersions")
    current = repl.session.state.version
    if isinstance(versions, list) and current and current not in versions:
        log.violation("server-card-versions",
                      f"server card does not list the negotiated version {current}", "SEP-2127",
                      level=Level.WARNING)
    print_findings(log.take_unshown())
