# Copyright 2026 Phil Pennock — see LICENSE file.

"""Against real MCP servers (official SDKs).  Opt-in: MCPTD_INTEGRATION=1.

Needs network access (PyPI, npm), uv and Node.js.  Run with:
    MCPTD_INTEGRATION=1 uv run pytest -m integration
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("MCPTD_INTEGRATION"), reason="set MCPTD_INTEGRATION=1 to run"),
]

HERE = Path(__file__).resolve().parent
PY_SDK = os.environ.get("MCPTD_PY_SDK", "mcp==2.3.0")
# 2.x needs Node 24; this release runs on Node 22.
EVERYTHING = os.environ.get("MCPTD_EVERYTHING", "@modelcontextprotocol/server-everything@2026.1.26")


def driver(*args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "mcp_test_driver", "--no-trace", *args],
                          capture_output=True, text=True, timeout=timeout, input="")


def py_sdk_server() -> list[str]:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not installed")
    return [uv, "run", "--no-project", "--with", PY_SDK, "python", str(HERE / "py_sdk_server.py")]


def everything_server() -> list[str]:
    npx = shutil.which("npx")
    if npx is None:
        pytest.skip("npx not installed")
    return [npx, "-y", EVERYTHING]


def test_python_sdk_is_modern_and_clean() -> None:
    r = driver("-c", "add a=2 b=40", "-c", "/check", *py_sdk_server())
    assert r.returncode == 0, r.stderr
    assert "2026-07-28 via stateless (modern)" in r.stdout
    assert "42" in r.stdout
    assert "[error/" not in r.stdout


def test_python_sdk_legacy_era() -> None:
    r = driver("--era", "legacy", "-c", "echo text=hi", *py_sdk_server())
    assert r.returncode == 0, r.stderr
    assert "2025-11-25 via initialize handshake" in r.stdout


def test_everything_server_tasks_and_elicitation() -> None:
    r = driver("-q", "--elicitation-auto", "-c", "/task simulate-research-query topic=mcp",
               "-c", "trigger-elicitation-request", *everything_server())
    assert r.returncode == 0, r.stderr
    assert "Research Report: mcp" in r.stdout
    assert "User provided the requested information" in r.stdout


def test_everything_server_check_json() -> None:
    r = driver("--check", "--json", *everything_server())
    assert r.returncode in (0, 1), r.stderr
    assert '"protocol"' in r.stdout
