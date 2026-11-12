# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for passive conformance checks, findings, and the report."""

from __future__ import annotations

import pytest

from mcp_test_driver import checks, versions
from mcp_test_driver.diagnostics import DiagnosticLog, Kind, Level
from mcp_test_driver.protocol import McpSession
from mcp_test_driver.report import capability_rows, print_report
from tests.fakes.memory import MemoryTransport


def _codes(log: DiagnosticLog) -> dict[str, Kind]:
    return {f.code: f.kind for f in log}


class TestDiagnosticLog:
    def test_dedupe_and_unshown(self) -> None:
        log = DiagnosticLog()
        log.note("a", "x")
        log.note("a", "x")
        log.violation("b", "y")
        assert len(log) == 2
        assert [f.code for f in log.take_unshown()] == ["a", "b"]
        assert log.take_unshown() == []
        assert log.worst() == Level.ERROR


class TestCapabilities:
    def test_beyond_unknown_and_removed(self) -> None:
        log = DiagnosticLog()
        caps = {"tools": {}, "completions": {}, "tasks": {}, "weird": {}}
        checks.check_capabilities(log, versions.V2024_11_05, caps)
        codes = _codes(log)
        assert codes["server-capability-beyond-version"] == Kind.BEYOND_CLAIMED
        assert codes["unknown-server-capability"] == Kind.NOTE
        log2 = DiagnosticLog()
        checks.check_capabilities(log2, versions.V2026_07_28, {"tasks": {}})
        assert _codes(log2)["server-capability-removed"] == Kind.VIOLATION

    def test_deprecated_logging_in_2026(self) -> None:
        log = DiagnosticLog()
        checks.check_capabilities(log, versions.V2026_07_28, {"logging": {}})
        assert _codes(log)["deprecated-logging"] == Kind.DEPRECATED

    def test_capability_must_be_object(self) -> None:
        log = DiagnosticLog()
        checks.check_capabilities(log, versions.V2025_11_25, {"tools": True})
        assert "capability-not-object" in _codes(log)


class TestTools:
    def _check(self, tools: list[dict], spec: versions.SpecVersion = versions.V2025_11_25) -> dict[str, Kind]:
        log = DiagnosticLog()
        checks.check_tools(log, spec, tools)
        return _codes(log)

    def test_name_rules_are_violations_from_2025_11_25(self) -> None:
        assert self._check([{"name": "has space", "inputSchema": {"type": "object"}}])["tool-name-format"] == Kind.VIOLATION
        older = self._check([{"name": "has space", "inputSchema": {"type": "object"}}], versions.V2025_06_18)
        assert older["tool-name-format"] == Kind.NOTE

    def test_long_name(self) -> None:
        assert "tool-name-format" in self._check([{"name": "x" * 129, "inputSchema": {"type": "object"}}])

    def test_duplicates_and_schema(self) -> None:
        codes = self._check([
            {"name": "a", "inputSchema": {"type": "object"}},
            {"name": "a", "inputSchema": {"type": "string"}},
            {"name": "b"},
            {"description": "nameless"},
        ])
        assert {"tool-name-duplicate", "tool-input-schema-type", "tool-input-schema", "tool-no-name"} <= set(codes)

    def test_beyond_claimed_fields(self) -> None:
        codes = self._check([{"name": "t", "inputSchema": {"type": "object"}, "title": "T",
                              "outputSchema": {"type": "object"}, "annotations": {}}], versions.V2024_11_05)
        assert codes["beyond-titles"] == Kind.BEYOND_CLAIMED
        assert codes["beyond-structured_output"] == Kind.BEYOND_CLAIMED
        assert codes["beyond-tool_annotations"] == Kind.BEYOND_CLAIMED

    def test_clean_tool_has_no_findings(self) -> None:
        assert self._check([{"name": "get_weather", "inputSchema": {"type": "object"}}]) == {}


def test_implementation_fields() -> None:
    log = DiagnosticLog()
    checks.check_implementation(log, versions.V2025_03_26, {"name": "s", "title": "S", "icons": []})
    codes = _codes(log)
    assert "server-info-no-version" in codes and "beyond-titles" in codes and "beyond-icons" in codes


def test_session_checks_batches_and_session_id() -> None:
    t = MemoryTransport()
    t.batches_received = 2  # type: ignore[attr-defined]
    s = McpSession(t)
    s.state.version = "2025-11-25"
    checks.check_session(s, {})
    assert "batch-not-allowed" in _codes(s.findings)


def test_capability_rows() -> None:
    rows = {r[0]: r for r in capability_rows({"tools": {"listChanged": True}, "custom": {}}, versions.V2025_06_18)}
    assert rows["tools"][1] == "yes" and "listChanged=True" in rows["tools"][2]
    assert rows["logging"][1] == "-"
    assert "tasks" not in rows  # not in 2025-06-18 and not advertised
    assert rows["custom"][2].startswith("unknown")


def test_print_report(capsys: pytest.CaptureFixture[str]) -> None:
    s = McpSession(MemoryTransport())
    s.state.version = "2025-06-18"
    s.state.server_info = {"name": "srv", "version": "1", "title": "Server"}
    s.state.instructions = "line\n" * 12
    s.state.how_chosen = ["initialize: offered 2025-11-25, server counter-offered 2025-06-18"]
    print_report(s)
    out = capsys.readouterr().out
    assert "2025-06-18 via initialize handshake" in out
    assert "srv 1  (Server)" in out
    assert "more line(s)" in out
    assert "counter-offered" in out


def test_preshown_findings_not_repeated() -> None:
    log = DiagnosticLog()
    log.note("a", "printed already", shown=True)
    log.note("b", "new")
    assert [f.code for f in log.take_unshown()] == ["b"]
    assert len(log) == 2
