"""Shared fixtures: fake MCP servers over stdio and HTTP."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.fakes.http_server import FakeHttpServer
from tests.fakes.proxy_server import FakeProxy

FAKES = Path(__file__).resolve().parent / "fakes"

SAMPLE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "echo",
        "description": "Echo the arguments back",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "What to say"},
                "mode": {"type": "string", "enum": ["loud", "quiet"]},
            },
        },
    },
]


@pytest.fixture
def fake_stdio(tmp_path: Path) -> Callable[[dict[str, Any]], list[str]]:
    """Return a factory: scenario → command line for a fake stdio server."""
    counter = iter(range(1000))

    def make(scenario: dict[str, Any]) -> list[str]:
        path = tmp_path / f"scenario{next(counter)}.json"
        path.write_text(json.dumps(scenario))
        return [sys.executable, str(FAKES / "stdio_server.py"), str(path)]

    return make


@pytest.fixture
def fake_http() -> Iterator[Callable[[dict[str, Any]], FakeHttpServer]]:
    servers: list[FakeHttpServer] = []

    def make(scenario: dict[str, Any]) -> FakeHttpServer:
        srv = FakeHttpServer(scenario).start()
        servers.append(srv)
        return srv

    yield make
    for srv in servers:
        srv.stop()


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "server-log.jsonl"


def read_log(path: Path) -> list[Any]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── TLS and proxies ────────────────────────────────────────────────────

# A name that resolves only through FakeProxy; see tests/fakes/proxy_server.py.
PROXIED_HOST = "mcp.example.test"


@dataclass(frozen=True)
class CertFiles:
    ca: Path  # the CA certificate (PEM) to trust
    cert: Path  # server certificate for PROXIED_HOST and 127.0.0.1
    key: Path


@pytest.fixture(scope="session")
def test_certs(tmp_path_factory: pytest.TempPathFactory) -> CertFiles:
    """A throwaway CA and server certificate, made with the openssl CLI."""
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is not installed")
    d = tmp_path_factory.mktemp("certs")
    ext = d / "server.ext"
    ext.write_text(
        f"subjectAltName=DNS:{PROXIED_HOST},IP:127.0.0.1\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n"
    )
    ec = ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes"]
    steps = [
        ["req", "-x509", "-new", *ec, "-keyout", "ca.key", "-out", "ca.pem", "-days", "2",
         "-subj", "/CN=mcp-test-driver test CA",
         "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign",
         "-addext", "subjectKeyIdentifier=hash"],
        ["req", "-new", *ec, "-keyout", "server.key", "-out", "server.csr", "-subj", f"/CN={PROXIED_HOST}"],
        ["x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
         "-out", "server.pem", "-days", "2", "-extfile", "server.ext"],
    ]
    for step in steps:
        r = subprocess.run([openssl, *step], cwd=d, capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip(f"openssl could not make test certificates: {r.stderr.strip()[:200]}")
    return CertFiles(ca=d / "ca.pem", cert=d / "server.pem", key=d / "server.key")


@pytest.fixture
def fake_proxy() -> Iterator[Callable[..., FakeProxy]]:
    proxies: list[FakeProxy] = []

    def make(**kw: Any) -> FakeProxy:
        proxy = FakeProxy(**kw).start()
        proxies.append(proxy)
        return proxy

    yield make
    for proxy in proxies:
        proxy.stop()

