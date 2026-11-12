# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for active probes, the check suite, and the non-interactive modes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import pytest

from mcp_test_driver import probe
from mcp_test_driver.negotiate import ConnectOptions, connect
from mcp_test_driver.probe import Outcome
from mcp_test_driver.protocol import McpSession
from mcp_test_driver.repl import Repl, SessionCache
from mcp_test_driver.transport import HttpTransport, StdioTransport
from tests.conftest import SAMPLE_TOOLS

LEGACY = {"versions": ["2025-11-25"], "tools": SAMPLE_TOOLS, "capabilities": {"tools": {}}}
MODERN = {"mode": "modern", "versions": ["2026-07-28"], "tools": SAMPLE_TOOLS}


def _run(args: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "mcp_test_driver", *args],
                          capture_output=True, text=True, timeout=60, **kw)


def _by_id(results: list[probe.Result]) -> dict[str, probe.Result]:
    return {r.id: r for r in results}


@pytest.fixture
def stdio_ctx(fake_stdio):  # noqa: ANN001, ANN201
    made: list[McpSession] = []

    def make(scenario: dict[str, Any], **opts: Any) -> probe.Context:
        t = StdioTransport(fake_stdio(scenario), timeout=10)
        t.trace = False
        s = McpSession(t)
        connect(s, ConnectOptions(probe_timeout=1, **opts))
        made.append(s)
        return probe.context(s, SessionCache.build(s))

    yield make
    for s in made:
        s.transport.close()


class TestProbes:
    def test_legacy_server(self, stdio_ctx) -> None:  # noqa: ANN001
        ctx = stdio_ctx(LEGACY)
        r = _by_id(probe.run(probe.PROBES, ctx))
        assert r["method:server/discover"].outcome == Outcome.UNCLEAR  # "not initialized"
        assert r["method:ping"].outcome == Outcome.SUPPORTED and r["method:ping"].claimed
        assert r["method:tasks/list"].outcome == Outcome.UNSUPPORTED
        assert r["method:tasks/list"].claimed is False  # tasks capability not advertised
        assert r["feature:batch"].outcome == Outcome.SUPPORTED
        codes = {f.code for f in ctx.session.findings}
        assert "probe-feature:batch" in codes  # batches after 2025-03-26
        assert not any(c.startswith("probe-method:tasks") for c in codes)

    def test_modern_server(self, stdio_ctx) -> None:  # noqa: ANN001
        r = _by_id(probe.run(probe.PROBES, stdio_ctx(MODERN)))
        assert r["method:server/discover"].outcome == Outcome.SUPPORTED
        assert r["method:ping"].outcome == Outcome.UNSUPPORTED and not r["method:ping"].claimed

    def test_matrix_rows(self, stdio_ctx) -> None:  # noqa: ANN001
        rows = {row[0]: row for row in probe.matrix_rows(probe.run(probe.PROBES, stdio_ctx(LEGACY)))}
        assert rows["ping"][2:] == ("yes", "yes")
        assert rows["JSON-RPC batch"][1] == "2025-03-26"

    def test_versions_sweep(self, stdio_ctx) -> None:  # noqa: ANN001
        ctx = stdio_ctx({**LEGACY, "versions": ["2025-03-26", "2025-11-25"]}, era="legacy")
        rows = dict(probe.probe_versions(ctx.session))
        assert rows["2025-03-26"].startswith("2025-03-26 via initialize")
        assert "counter-offer" in rows["2024-11-05"]
        assert rows["2026-07-28"].startswith("refused")
        assert ctx.session.state.version == "2025-11-25"  # reconnected as before


class TestChecks:
    def test_stdio_checks(self, stdio_ctx) -> None:  # noqa: ANN001
        r = _by_id(probe.run(probe.CHECKS, stdio_ctx({**LEGACY, "capabilities": {"tools": {}, "completions": {}, "prompts": {}},
                                                     "prompts": [{"name": "p", "arguments": [{"name": "a"}]}],
                                                     "completions": ["x"]})))
        assert r["tools-list"].outcome == Outcome.PASS
        assert r["check:unknown-method"].outcome == Outcome.PASS
        assert r["check:unknown-tool"].outcome == Outcome.PASS
        assert r["sep-2164-resource-not-found"].outcome == Outcome.PASS  # -32002 before 2026
        assert r["check:parse-error"].outcome == Outcome.PASS
        assert r["completion-complete"].outcome == Outcome.PASS
        assert r["dns-rebinding-protection"].outcome == Outcome.SKIP

    def test_modern_resource_not_found(self, stdio_ctx) -> None:  # noqa: ANN001
        r = _by_id(probe.run(probe.CHECKS, stdio_ctx(MODERN)))
        assert r["sep-2164-resource-not-found"].outcome == Outcome.PASS  # -32602 in 2026
        assert r["caching"].outcome == Outcome.PASS

    def test_http_header_checks_fail_on_lenient_server(self, fake_http) -> None:  # noqa: ANN001
        srv = fake_http(MODERN)  # the fake does not validate headers or Origin
        t = HttpTransport(srv.url, timeout=10)
        t.trace = False
        s = McpSession(t)
        connect(s, ConnectOptions())
        r = _by_id(probe.run(probe.CHECKS, probe.context(s, SessionCache.build(s))))
        assert r["check:protocol-version-header"].outcome == Outcome.FAIL
        assert r["check:http-header-validation"].outcome == Outcome.FAIL
        assert r["dns-rebinding-protection"].outcome == Outcome.FAIL
        t.close()


class TestCli:
    def test_check_exit_status_and_json(self, fake_stdio) -> None:  # noqa: ANN001
        r = _run(["--check", "--json", *fake_stdio(LEGACY)])
        assert r.returncode == 0, r.stderr
        doc = json.loads(r.stdout)
        assert doc["result"] == "pass" and doc["protocol"]["version"] == "2025-11-25"
        assert {c["id"] for c in doc["checks"]} >= {"tools-list", "check:unknown-method"}

    def test_check_fails_on_bad_server(self, fake_stdio) -> None:  # noqa: ANN001
        bad = {**LEGACY, "tools": [{"name": "a", "inputSchema": {"type": "object"}}, {"name": "a"}]}
        r = _run(["--check", "-q", "--no-trace", *fake_stdio(bad)])
        assert r.returncode == 1
        assert "Result: FAIL" in r.stdout

    def test_check_unreachable_is_2(self) -> None:
        assert _run(["--check", "/nonexistent/server"]).returncode == 2
        assert _run(["--check", "--no-trace", "http://127.0.0.1:1/mcp"]).returncode == 2
        assert _run(["/nonexistent/server"]).returncode == 1  # interactive mode keeps 1

    def test_probe_versions_cli(self, fake_stdio) -> None:  # noqa: ANN001
        r = _run(["--probe-versions", "--json", *fake_stdio({**LEGACY, "versions": ["2025-06-18"]})])
        doc = json.loads(r.stdout)
        assert {v["offered"] for v in doc["versions"]} == {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"}

    def test_exec(self, fake_stdio) -> None:  # noqa: ANN001
        r = _run(["-q", "--no-trace", "-c", "echo text=one", "-c", "/ping", *fake_stdio(LEGACY)])
        assert r.returncode == 0 and '"text": "one"' in r.stdout
        r = _run(["-q", "--no-trace", "-c", "/send no/such", *fake_stdio(LEGACY)])
        assert r.returncode == 1

    def test_conformance_client_mode(self, fake_stdio) -> None:  # noqa: ANN001
        env = {**os.environ, "MCP_CONFORMANCE_SCENARIO": "tools_call", "MCP_CONFORMANCE_PROTOCOL_VERSION": "2025-11-25"}
        r = _run(["--conformance-client", *fake_stdio({**LEGACY, "log_path": "/dev/null"})], env=env)
        assert r.returncode == 0, r.stderr
        assert '"a": 2' in r.stdout

    def test_conformance_tool_calls_from_context(self, fake_stdio) -> None:  # noqa: ANN001
        ctx = {"toolCalls": [{"name": "echo", "arguments": {"text": "ctx"}}]}
        env = {**os.environ, "MCP_CONFORMANCE_SCENARIO": "x", "MCP_CONFORMANCE_CONTEXT": json.dumps(ctx)}
        r = _run(["--conformance-client", *fake_stdio(LEGACY)], env=env)
        assert '"text": "ctx"' in r.stdout


def test_repl_commands(fake_stdio, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ANN001
    t = StdioTransport(fake_stdio(LEGACY), timeout=10)
    t.trace = False
    s = McpSession(t)
    connect(s, ConnectOptions(era="legacy"))
    r = Repl(s)
    r.handle_line("/probe")
    assert "JSON-RPC batch" in capsys.readouterr().out
    r.handle_line("/matrix")
    assert "claimed" in capsys.readouterr().out
    r.handle_line("/check")
    assert "unknown method" in capsys.readouterr().out
    t.close()


def test_auto_elicitation_defaults() -> None:
    from mcp_test_driver.interactive import AutoElicitationHandler

    result = AutoElicitationHandler().handle({"requestedSchema": {"type": "object", "properties": {
        "s": {"type": "string", "default": "d"}, "n": {"type": "integer"}, "b": {"type": "boolean"},
        "e": {"type": "string", "enum": ["x", "y"]}, "opt": {"type": "string"}}, "required": ["n", "b", "e"]}})
    assert result == {"action": "accept", "content": {"s": "d", "n": 0, "b": True, "e": "x"}}
