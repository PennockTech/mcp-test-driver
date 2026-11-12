# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for the command-line entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mcp_test_driver import cli
from tests.conftest import SAMPLE_TOOLS


def _run(args: list[str], stdin: str = "", timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_test_driver", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestHelp:
    @pytest.mark.parametrize("flag", ["--help", "-h"])
    def test_help(self, flag: str) -> None:
        r = _run([flag])
        assert r.returncode == 0
        assert "usage:" in r.stdout and "--protocol-version" in r.stdout
        assert "2026-07-28" in r.stdout

    def test_no_args_shows_usage(self) -> None:
        r = _run([])
        assert r.returncode == 1 and "usage:" in r.stdout

    def test_version(self) -> None:
        r = _run(["--version"])
        assert r.returncode == 0 and "mcp-test-driver" in r.stdout


class TestArgv:
    def test_bare_roots_means_cwd(self) -> None:
        argv = cli._normalise_argv(["--roots", "aifr", "mcp"])
        assert argv == [f"--roots={Path.cwd()}", "aifr", "mcp"]

    def test_options_after_target_belong_to_server(self) -> None:
        args = cli.build_parser().parse_args(cli._normalise_argv(["--no-trace", "srv", "--roots", "-x"]))
        assert args.target == ["srv", "--roots", "-x"]
        assert args.roots is None and args.trace is False

    def test_parse_headers(self) -> None:
        assert cli.parse_headers(["X-A: 1", "X-B:two words"]) == {"X-A": "1", "X-B": "two words"}
        with pytest.raises(ValueError):
            cli.parse_headers(["no-colon"])
        with pytest.raises(ValueError):
            cli.parse_headers(["X: a\r\nY: b"])

    def test_bearer_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_TOKEN", "abc")
        args = cli.build_parser().parse_args(["--bearer-env", "MY_TOKEN", "https://example.com/mcp"])
        t = cli.make_transport(args)
        assert t.wire.extra_headers["Authorization"] == "Bearer abc"

    def test_bearer_env_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NOPE_TOKEN", raising=False)
        args = cli.build_parser().parse_args(["--bearer-env", "NOPE_TOKEN", "https://example.com/mcp"])
        with pytest.raises(ValueError, match="NOPE_TOKEN"):
            cli.make_transport(args)

    def test_http_target_takes_no_args(self) -> None:
        args = cli.build_parser().parse_args(["https://example.com/mcp", "extra"])
        with pytest.raises(ValueError):
            cli.make_transport(args)


class TestTransportDetection:
    @pytest.mark.parametrize("url", ["http://127.0.0.1:1/nonexistent", "https://127.0.0.1:1/nonexistent"])
    def test_url_uses_http(self, url: str) -> None:
        r = _run([url])
        assert r.returncode != 0
        assert "Connection failed" in r.stderr or "TLS" in r.stderr

    def test_command_uses_stdio(self) -> None:
        r = _run(["/nonexistent/command"])
        assert r.returncode != 0
        assert "Command not found" in r.stderr


class TestEndToEnd:
    def test_stdio_session(self, fake_stdio) -> None:  # noqa: ANN001
        cmd = fake_stdio({"tools": SAMPLE_TOOLS, "versions": ["2025-06-18"]})
        r = _run(["--no-trace", *cmd], stdin="echo text=hi\n/quit\n")
        assert r.returncode == 0, r.stderr
        assert "2025-06-18 via initialize handshake" in r.stdout
        assert '"text": "hi"' in r.stdout

    def test_quiet_skips_report(self, fake_stdio) -> None:  # noqa: ANN001
        r = _run(["-q", "--no-trace", *fake_stdio({"tools": SAMPLE_TOOLS})], stdin="")
        assert r.returncode == 0
        assert "Server capabilities" not in r.stdout

    def test_protocol_version_offered(self, fake_stdio) -> None:  # noqa: ANN001
        cmd = fake_stdio({"tools": SAMPLE_TOOLS, "versions": ["2024-11-05", "2025-11-25"]})
        r = _run(["--no-trace", "--protocol-version", "2024-11-05", *cmd], stdin="")
        assert "2024-11-05 via initialize handshake" in r.stdout

    def test_http_session(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http({"tools": SAMPLE_TOOLS, "session_id": "s-1"})
        r = _run(["--no-trace", srv.url], stdin="echo text=web\n")
        assert r.returncode == 0, r.stderr
        assert "Mcp-Session-Id s-1" in r.stdout
        assert '"text": "web"' in r.stdout
        assert any(m == "DELETE" for (m, *_) in srv.requests)

    def test_initialize_failure_exits_nonzero(self, fake_stdio) -> None:  # noqa: ANN001
        r = _run(["--no-trace", "--era", "legacy", *fake_stdio({"mode": "modern", "versions": ["2026-07-28"]})])
        assert r.returncode == 1 and "MCP error" in r.stderr

    def test_auto_era_reaches_modern_server(self, fake_stdio) -> None:  # noqa: ANN001
        cmd = fake_stdio({"mode": "modern", "versions": ["2026-07-28"], "tools": SAMPLE_TOOLS})
        r = _run(["--no-trace", *cmd], stdin="echo text=new\n")
        assert r.returncode == 0, r.stderr
        assert "2026-07-28 via stateless (modern)" in r.stdout
        assert '"text": "new"' in r.stdout

    def test_era_modern_against_legacy_server_fails(self, fake_stdio) -> None:  # noqa: ANN001
        r = _run(["--no-trace", "--era", "modern", "--probe-timeout", "1", *fake_stdio({"tools": SAMPLE_TOOLS})])
        assert r.returncode == 1 and "stateless" in r.stderr
