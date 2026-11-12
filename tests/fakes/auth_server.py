"""A fake OAuth authorization server for the HTTP fake MCP server.

Enabled by the scenario key "oauth" (a dict; all keys optional):
    iss                "correct" | "missing" | "wrong"   (authorization response iss)
    iss_advertised     advertise authorization_response_iss_parameter_supported
    cimd               advertise client_id_metadata_document_supported
    registration       offer dynamic client registration
    device             offer the device authorization grant
    no_s256            omit code_challenge_methods_supported
    auth_methods       token_endpoint_auth_methods_supported
    clients            {client_id: secret} for confidential clients
    scopes_supported   PRM and AS scopes_supported
    challenge_scope    scope="..." in the 401 challenge
    no_resource_metadata  send a bare "Bearer" challenge
    tool_scopes        {tool: scope} the tool needs (403 insufficient_scope)
    prm_override / as_override   dicts merged into the metadata
    deny               the user declines at /authorize
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode


class FakeAuth:
    def __init__(self, cfg: dict[str, Any], origin: str) -> None:
        self.cfg = cfg
        self.origin = origin
        self.codes: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, str] = {}  # access token -> scope
        self.refresh: dict[str, str] = {}
        self.log: list[tuple[str, dict[str, Any]]] = []
        self.n = 0
        self.device_polls = 0

    @property
    def resource(self) -> str:
        return f"{self.origin}/mcp"

    def _id(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}-{self.n}"

    # ── metadata ───────────────────────────────────────────────────────

    def prm(self) -> dict[str, Any]:
        d = {"resource": self.resource, "authorization_servers": [self.origin],
             "scopes_supported": self.cfg.get("scopes_supported", ["mcp"])}
        return {**d, **self.cfg.get("prm_override", {})}

    def as_metadata(self) -> dict[str, Any]:
        c = self.cfg
        d: dict[str, Any] = {
            "issuer": self.origin,
            "authorization_endpoint": f"{self.origin}/authorize",
            "token_endpoint": f"{self.origin}/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials", "urn:ietf:params:oauth:grant-type:device_code"],
            "token_endpoint_auth_methods_supported": c.get("auth_methods", ["none", "client_secret_basic", "client_secret_post"]),
            "scopes_supported": c.get("scopes_supported", ["mcp"]),
        }
        if not c.get("no_s256"):
            d["code_challenge_methods_supported"] = ["S256"]
        if c.get("cimd"):
            d["client_id_metadata_document_supported"] = True
        if c.get("registration"):
            d["registration_endpoint"] = f"{self.origin}/register"
        if c.get("device"):
            d["device_authorization_endpoint"] = f"{self.origin}/device"
        if c.get("iss_advertised"):
            d["authorization_response_iss_parameter_supported"] = True
        return {**d, **c.get("as_override", {})}

    # ── the protected resource ────────────────────────────────────────

    def challenge(self, error: str | None = None, scope: str | None = None) -> str:
        if self.cfg.get("no_resource_metadata") and not error:
            return "Bearer"
        parts = [f'resource_metadata="{self.origin}/.well-known/oauth-protected-resource/mcp"']
        scope = scope or self.cfg.get("challenge_scope")
        if scope:
            parts.append(f'scope="{scope}"')
        if error:
            parts.insert(0, f'error="{error}"')
        return "Bearer " + ", ".join(parts)

    def check(self, headers: dict[str, str], body: Any) -> tuple[int, str] | None:
        """None if the request is authorized, else (status, WWW-Authenticate)."""
        auth = headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else None
        if token not in self.tokens:
            return 401, self.challenge()
        if isinstance(body, dict) and body.get("method") == "tools/call":
            need = self.cfg.get("tool_scopes", {}).get((body.get("params") or {}).get("name"))
            if need and need not in self.tokens[token].split():
                return 403, self.challenge("insufficient_scope", need)
        return None

    # ── endpoints ──────────────────────────────────────────────────────

    def get(self, path: str, query: str) -> tuple[int, dict[str, str], bytes] | None:
        if path == "/.well-known/oauth-protected-resource/mcp":
            return 200, {"Content-Type": "application/json"}, json.dumps(self.prm()).encode()
        if path == "/.well-known/oauth-authorization-server":
            return 200, {"Content-Type": "application/json"}, json.dumps(self.as_metadata()).encode()
        if path == "/authorize":
            return self._authorize({k: v[0] for k, v in parse_qs(query).items()})
        return None

    def _authorize(self, q: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        self.log.append(("authorize", q))
        sep = "&" if "?" in q["redirect_uri"] else "?"
        if self.cfg.get("deny"):
            loc = q["redirect_uri"] + sep + urlencode({"error": "access_denied", "state": q.get("state", "")})
            return 302, {"Location": loc}, b""
        code = self._id("code")
        self.codes[code] = q
        params = {"code": code, "state": q.get("state", "")}
        iss = self.cfg.get("iss", "correct" if self.cfg.get("iss_advertised") else None)
        if iss == "correct":
            params["iss"] = self.origin
        elif iss == "wrong":
            params["iss"] = "https://attacker.example"
        return 302, {"Location": q["redirect_uri"] + sep + urlencode(params)}, b""

    def post(self, path: str, body: bytes, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes] | None:
        if path == "/register":
            req = json.loads(body)
            self.log.append(("register", req))
            return 201, {"Content-Type": "application/json"}, json.dumps({"client_id": self._id("dyn"), **req}).encode()
        form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        if path == "/device":
            self.log.append(("device", form))
            return 200, {"Content-Type": "application/json"}, json.dumps({
                "device_code": "dev-1", "user_code": "ABCD-EFGH", "verification_uri": f"{self.origin}/activate",
                "interval": 0, "expires_in": 30}).encode()
        if path == "/token":
            return self._token(form, headers)
        return None

    def _client(self, form: dict[str, str], headers: dict[str, str]) -> tuple[str | None, str]:
        auth = headers.get("Authorization", "")
        if auth.startswith("Basic "):
            cid, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
            cid, secret = unquote(cid), unquote(secret)
            ok = self.cfg.get("clients", {}).get(cid) == secret
            return (cid if ok else None), "client_secret_basic"
        cid = form.get("client_id")
        if "client_secret" in form:
            ok = self.cfg.get("clients", {}).get(cid) == form["client_secret"]
            return (cid if ok else None), "client_secret_post"
        return cid, "none"

    def _issue(self, scope: str) -> bytes:
        tok, ref = self._id("tok"), self._id("ref")
        self.tokens[tok] = scope
        self.refresh[ref] = scope
        return json.dumps({"access_token": tok, "token_type": "Bearer", "expires_in": 3600,
                           "refresh_token": ref, "scope": scope}).encode()

    def _token(self, form: dict[str, str], headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        cid, method = self._client(form, headers)
        self.log.append(("token", {**form, "_auth_method": method}))
        bad = (400, {"Content-Type": "application/json"})
        if cid is None:
            return *bad, b'{"error":"invalid_client"}'
        grant = form.get("grant_type")
        if grant == "authorization_code":
            info = self.codes.pop(form.get("code", ""), None)
            if info is None or info["redirect_uri"] != form.get("redirect_uri"):
                return *bad, b'{"error":"invalid_grant"}'
            digest = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
            if digest != info.get("code_challenge"):
                return *bad, b'{"error":"invalid_grant","error_description":"PKCE"}'
            return 200, {"Content-Type": "application/json"}, self._issue(info.get("scope", ""))
        if grant == "refresh_token":
            scope = self.refresh.get(form.get("refresh_token", ""))
            if scope is None:
                return *bad, b'{"error":"invalid_grant"}'
            return 200, {"Content-Type": "application/json"}, self._issue(scope)
        if grant == "client_credentials":
            if method == "none":
                return *bad, b'{"error":"invalid_client"}'
            return 200, {"Content-Type": "application/json"}, self._issue(form.get("scope", ""))
        if grant == "urn:ietf:params:oauth:grant-type:device_code":
            self.device_polls += 1
            if self.device_polls < 2:
                return *bad, b'{"error":"authorization_pending"}'
            return 200, {"Content-Type": "application/json"}, self._issue(form.get("scope", "mcp"))
        return *bad, b'{"error":"unsupported_grant_type"}'
