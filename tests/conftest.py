"""Shared fixtures: fake MCP servers over stdio and HTTP."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.fakes.http_server import FakeHttpServer

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
