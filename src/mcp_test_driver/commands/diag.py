# Copyright 2026 Phil Pennock — see LICENSE file.

"""Diagnostics builtins: the connection report and findings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..color import bold, dim, green, red, yellow
from ..sanitize import safe_json, sanitize
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


_OUTCOME_COLOURS = {"pass": green, "supported": green, "fail": red, "error": red, "warn": yellow}


def print_results(results: list[Any]) -> None:
    for r in results:
        colour = _OUTCOME_COLOURS.get(str(r.outcome), dim)
        detail = f"  {dim(sanitize(r.detail))}" if r.detail else ""
        print(f"  {colour(f'{r.outcome:<11}')} {r.title}{detail}")


@command(
    "/probe",
    group="Diagnostics",
    summary="Probe which features the server actually supports, whatever it claims",
    usage="/probe            probe methods and transport features\n"
          "/probe versions   reconnect offering each known revision (restarts stdio servers)",
    words=("versions",),
)
def cmd_probe(repl: Repl, rest: str) -> None:
    from .. import probe

    if rest.strip() == "versions":
        for offered, outcome in probe.probe_versions(repl.session):
            print(f"  {bold(offered)}  {sanitize(outcome)}")
        repl.cache.fetch_advertised(repl.session)
        return
    results = probe.run(probe.PROBES, probe.context(repl.session, repl.cache))
    repl.last_probe = results
    print_results(results)


@command("/matrix", group="Diagnostics",
         summary="Claimed vs observed support for probed features (runs /probe if needed)")
def cmd_matrix(repl: Repl, rest: str) -> None:
    from .. import probe

    if not repl.last_probe:
        repl.last_probe = probe.run(probe.PROBES, probe.context(repl.session, repl.cache))
    print(f"  {bold('feature'):<44} {bold('from'):<20} {bold('claimed'):<16} {bold('observed')}")
    for title, intro, claimed, observed in probe.matrix_rows(repl.last_probe):
        mark = green(observed) if observed == claimed else yellow(observed)
        print(f"  {title:<36} {intro:<12} {claimed:<8} {mark}")


@command("/check", group="Diagnostics",
         summary="Run the conformance checks (error handling, headers, lists, caching)")
def cmd_check(repl: Repl, rest: str) -> None:
    from .. import probe

    results = probe.run(probe.CHECKS, probe.context(repl.session, repl.cache))
    print_results(results)
