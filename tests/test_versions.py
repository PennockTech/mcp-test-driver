"""Tests for the spec-revision registry, cross-checked against vendored schemas."""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

import pytest

from mcp_test_driver import versions
from mcp_test_driver.versions import Feature

SPECS_DIR = Path(__file__).resolve().parent.parent / "docs" / "specs"


@cache
def _schema(rev: str) -> dict:
    data = json.loads((SPECS_DIR / rev / "schema.json").read_text())
    return data.get("definitions") or data["$defs"]


def _schema_methods(rev: str) -> set[str]:
    out: set[str] = set()
    for d in _schema(rev).values():
        method = d.get("properties", {}).get("method", {})
        if "const" in method:
            out.add(method["const"])
    return out


def _schema_caps(rev: str, which: str) -> set[str]:
    return set(_schema(rev)[which].get("properties", {}))


def _schema_content_types(rev: str) -> set[str]:
    out: set[str] = set()
    for name, d in _schema(rev).items():
        if not name.endswith("Content") and name not in ("EmbeddedResource", "ResourceLink"):
            continue
        t = d.get("properties", {}).get("type", {})
        if isinstance(t.get("const"), str):
            out.add(t["const"])
    return out


@pytest.mark.parametrize("rev", versions.KNOWN_VERSIONS)
class TestAgainstSchema:
    def test_methods(self, rev: str) -> None:
        assert versions.SPECS[rev].methods == _schema_methods(rev)

    def test_client_capabilities(self, rev: str) -> None:
        assert versions.SPECS[rev].client_capabilities == _schema_caps(
            rev, "ClientCapabilities"
        )

    def test_server_capabilities(self, rev: str) -> None:
        assert versions.SPECS[rev].server_capabilities == _schema_caps(
            rev, "ServerCapabilities"
        )

    def test_content_types(self, rev: str) -> None:
        assert versions.SPECS[rev].content_types == _schema_content_types(rev)

    def test_latest_protocol_version_constant(self, rev: str) -> None:
        ts = (SPECS_DIR / rev / "schema.ts").read_text()
        m = re.search(r'LATEST_PROTOCOL_VERSION = "([^"]+)"', ts)
        assert m and m.group(1) == rev


def test_every_vendored_revision_is_registered() -> None:
    vendored = {
        p.name for p in SPECS_DIR.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d\d-\d\d", p.name)
    }
    assert vendored == set(versions.KNOWN_VERSIONS)


def test_draft_matches_latest_or_needs_new_revision() -> None:
    """If the draft gains a version string or new methods, a new entry is due."""
    ts = (SPECS_DIR / "draft" / "schema.ts").read_text()
    m = re.search(r'LATEST_PROTOCOL_VERSION = "([^"]+)"', ts)
    assert m and m.group(1) == versions.LATEST
    assert _schema_methods("draft") == versions.SPECS[versions.LATEST].methods


class TestHelpers:
    def test_ordering_and_eras(self) -> None:
        assert list(versions.KNOWN_VERSIONS) == sorted(versions.KNOWN_VERSIONS)
        assert versions.LATEST == "2026-07-28"
        assert versions.LATEST_HANDSHAKE == "2025-11-25"
        assert versions.LATEST_MODERN == "2026-07-28"
        assert versions.era_of("2025-06-18") == "handshake"
        assert versions.era_of("2026-07-28") == "modern"

    def test_alias(self) -> None:
        assert versions.get("2024-10-07") is versions.V2024_11_05
        assert versions.is_known("2024-10-07")

    def test_spec_for_unknown_versions(self) -> None:
        assert versions.spec_for("2099-01-01").name == versions.LATEST
        assert versions.spec_for("2025-07-01").name == "2025-06-18"
        assert versions.spec_for("2000-01-01").name == "2024-11-05"
        assert versions.spec_for(None).name == versions.LATEST

    def test_introduced(self) -> None:
        assert versions.introduced_method("elicitation/create") == "2025-06-18"
        assert versions.introduced_method("server/discover") == "2026-07-28"
        assert versions.introduced_method("bogus/method") is None
        assert versions.introduced_capability("server", "completions") == "2025-03-26"
        assert versions.introduced_content_type("resource_link") == "2025-06-18"
        assert versions.introduced_feature(Feature.MRTR) == "2026-07-28"

    def test_removed(self) -> None:
        assert versions.removed_method("ping") == "2026-07-28"
        assert versions.removed_method("tasks/result") == "2026-07-28"
        assert versions.removed_method("tools/call") is None

    def test_feature_spans(self) -> None:
        assert versions.V2025_03_26.has(Feature.BATCHING)
        assert not versions.V2025_06_18.has(Feature.BATCHING)
        assert versions.V2025_11_25.has(Feature.SESSIONS)
        assert not versions.V2026_07_28.has(Feature.SESSIONS)
        assert versions.V2026_07_28.has(Feature.PROTOCOL_VERSION_HEADER)
