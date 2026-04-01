# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for ANSI color helpers."""

import io

from mcp_test_driver.color import _colour


class TestColour:
    """Tests for the _colour function and TTY detection."""

    def test_non_tty_returns_plain(self) -> None:
        stream = io.StringIO()
        assert _colour("31", "hello", stream) == "hello"

    def test_tty_returns_escaped(self) -> None:
        """Simulate a TTY stream."""

        class FakeTTY(io.StringIO):
            def isatty(self) -> bool:
                return True

        stream = FakeTTY()
        result = _colour("31", "hello", stream)
        assert result == "\033[31mhello\033[0m"

    def test_empty_string(self) -> None:
        class FakeTTY(io.StringIO):
            def isatty(self) -> bool:
                return True

        assert _colour("1", "", FakeTTY()) == "\033[1m\033[0m"

    def test_code_preserved(self) -> None:
        class FakeTTY(io.StringIO):
            def isatty(self) -> bool:
                return True

        result = _colour("36", "test", FakeTTY())
        assert "\033[36m" in result
