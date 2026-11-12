# Copyright 2026 Phil Pennock — see LICENSE file.

"""Fetching JSON documents a server points us at (auth metadata, server cards).

The URLs come from the server, so the fetch is deliberately narrow:
GET only, no redirects, HTTPS required except for loopback hosts, a small
size cap, and no credentials sent unless the caller passes them.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import urllib3
import urllib3.exceptions
import urllib3.util

from .sanitize import sanitize

MAX_METADATA_BYTES = 1024 * 1024
FETCH_TIMEOUT = 10.0


@dataclass
class Fetched:
    url: str
    status: int | None
    data: Any = None  # parsed JSON, when the body was JSON
    error: str | None = None
    content_type: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None and self.data is not None

    def describe(self) -> str:
        if self.error:
            return sanitize(self.error)
        if self.status != 200:
            return f"HTTP {self.status}"
        return "ok" if self.data is not None else "not JSON"


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def url_allowed(url: str) -> str | None:
    """None if we may fetch url, else the reason not to."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return None
    if parts.scheme == "http" and is_loopback(parts.hostname):
        return None
    return f"refusing to fetch {sanitize(url)}: only https (or http to loopback) is allowed"


def same_origin(a: str, b: str) -> bool:
    pa, pb = urlsplit(a), urlsplit(b)

    def port(p: Any) -> int | None:
        return p.port or {"http": 80, "https": 443}.get(p.scheme)

    return (pa.scheme, pa.hostname, port(pa)) == (pb.scheme, pb.hostname, port(pb))


class JsonFetcher:
    def __init__(self, *, verify_tls: bool = True, timeout: float = FETCH_TIMEOUT) -> None:
        self._pool = urllib3.PoolManager(
            retries=urllib3.util.Retry(total=1, redirect=0),
            timeout=urllib3.util.Timeout(connect=timeout, read=timeout),
            cert_reqs="CERT_REQUIRED" if verify_tls else "CERT_NONE",
        )

    def get(self, url: str, headers: dict[str, str] | None = None) -> Fetched:
        reason = url_allowed(url)
        if reason:
            return Fetched(url, None, error=reason)
        try:
            resp = self._pool.request(
                "GET", url, headers={"Accept": "application/json", **(headers or {})},
                preload_content=False, redirect=False,
            )
        except urllib3.exceptions.HTTPError as e:
            return Fetched(url, None, error=f"fetch failed: {e}")
        try:
            ctype = resp.headers.get("Content-Type", "")
            if 300 <= resp.status < 400:
                return Fetched(url, resp.status, error=f"redirect to {resp.headers.get('Location', '?')} not followed",
                               content_type=ctype)
            body = resp.read(MAX_METADATA_BYTES + 1)
            if len(body) > MAX_METADATA_BYTES:
                return Fetched(url, resp.status, error="document too large", content_type=ctype)
            data = None
            if body.strip():
                try:
                    data = json.loads(body)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    data = None
            return Fetched(url, resp.status, data, content_type=ctype)
        finally:
            resp.release_conn()

    def post_form(self, url: str, fields: dict[str, str], headers: dict[str, str] | None = None) -> Fetched:
        """POST application/x-www-form-urlencoded (OAuth token endpoints)."""
        from urllib.parse import urlencode

        return self._post(url, urlencode(fields).encode(),
                          {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})

    def post_json(self, url: str, body: Any, headers: dict[str, str] | None = None) -> Fetched:
        return self._post(url, json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})})

    def _post(self, url: str, body: bytes, headers: dict[str, str]) -> Fetched:
        reason = url_allowed(url)
        if reason:
            return Fetched(url, None, error=reason)
        try:
            resp = self._pool.request("POST", url, body=body, headers={"Accept": "application/json", **headers},
                                      preload_content=False, redirect=False)
        except urllib3.exceptions.HTTPError as e:
            return Fetched(url, None, error=f"request failed: {e}")
        try:
            raw = resp.read(MAX_METADATA_BYTES + 1)
            data = None
            if raw.strip() and len(raw) <= MAX_METADATA_BYTES:
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    data = None
            return Fetched(url, resp.status, data, content_type=resp.headers.get("Content-Type", ""))
        finally:
            resp.release_conn()
