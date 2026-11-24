# Copyright 2026 Phil Pennock — see LICENSE file.

"""mcp-test-driver: Interactive REPL for testing any MCP server."""

from importlib.metadata import PackageNotFoundError, version

# The version lives only in pyproject.toml, and only the release tasks in
# Taskfile.yml change it: between releases it is the next patch as a dev
# release (e.g. 0.2.1.dev0), and at a tagged commit it is exactly the tag.
try:
    __version__ = version("mcp-test-driver")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0+unknown"
