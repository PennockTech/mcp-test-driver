# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for CLI entry point."""

from __future__ import annotations

import subprocess
import sys


class TestCliHelp:
    """Tests for CLI help and argument handling."""

    def test_help_flag(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "mcp_test_driver", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Usage:" in result.stdout

    def test_h_flag(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "mcp_test_driver", "-h"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Usage:" in result.stdout

    def test_no_args_shows_usage(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "mcp_test_driver"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "Usage:" in result.stdout


class TestTransportDetection:
    """Tests that the CLI correctly detects HTTP vs stdio."""

    def test_http_url_detected(self) -> None:
        """Verify that an HTTP URL triggers the HTTP transport path.

        We test this by passing a bogus URL — it should fail trying
        to connect, not fail with "command not found".
        """
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mcp_test_driver",
                "http://localhost:1/nonexistent",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Should fail with a connection error, not a "file not found"
        assert result.returncode != 0
        # The error should be about connecting, not about missing commands
        combined = result.stdout + result.stderr
        assert "No such file or directory" not in combined

    def test_https_url_detected(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mcp_test_driver",
                "https://localhost:1/nonexistent",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "No such file or directory" not in combined

    def test_command_triggers_stdio(self) -> None:
        """Verify that a non-URL argument triggers stdio transport.

        We pass a nonexistent command — it should fail with a file/spawn
        error, not an HTTP error.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mcp_test_driver",
                "/nonexistent/command",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
