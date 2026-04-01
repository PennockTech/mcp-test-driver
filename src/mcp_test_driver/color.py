# Copyright 2026 Phil Pennock — see LICENSE file.

"""ANSI color helpers for terminal output."""

import sys
from typing import IO


def _colour(code: str, s: str, stream: IO[str]) -> str:
    if stream.isatty():
        return f"\033[{code}m{s}\033[0m"
    return s


def _err(code: str, s: str) -> str:
    return _colour(code, s, sys.stderr)


def _out(code: str, s: str) -> str:
    return _colour(code, s, sys.stdout)


def cyan(s: str) -> str:
    return _err("36", s)


def yellow(s: str) -> str:
    return _err("33", s)


def bold_err(s: str) -> str:
    return _err("1", s)


def bold(s: str) -> str:
    return _out("1", s)


def red(s: str) -> str:
    return _out("31", s)


def dim(s: str) -> str:
    return _out("2", s)


def green(s: str) -> str:
    return _out("32", s)


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)
