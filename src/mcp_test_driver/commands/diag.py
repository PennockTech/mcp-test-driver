# Copyright 2026 Phil Pennock — see LICENSE file.

"""Diagnostics builtins: the connection report and findings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..color import dim
from ..sanitize import safe_json
from . import command

if TYPE_CHECKING:
    from ..repl import Repl


@command("/info", alias="/i", group="Diagnostics",
         summary="Show the connection report: protocol, server, capabilities, findings")
def cmd_info(repl: Repl, rest: str) -> None:
    from ..report import print_report

    print_report(repl.session, repl.cache, all_findings=True)


@command("/findings", alias="/f", group="Diagnostics",
         summary="List conformance findings so far", usage="/findings [clear]", words=("clear",))
def cmd_findings(repl: Repl, rest: str) -> None:
    from ..diagnostics import print_findings

    if rest.strip() == "clear":
        repl.session.findings.clear()
        print(dim("Findings cleared."))
        return
    if not repl.session.findings:
        print(dim("No findings."))
        return
    print_findings(list(repl.session.findings))
    repl.session.findings.take_unshown()


@command("/last", group="Diagnostics", summary="Show the last response as raw JSON")
def cmd_last(repl: Repl, rest: str) -> None:
    if repl.last_response is None:
        print(dim("No response yet."))
        return
    print(safe_json(repl.last_response))
