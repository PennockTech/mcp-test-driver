# Copyright 2026 Phil Pennock — see LICENSE file.

"""A small MCP server built on the official Python SDK, for the integration tests."""

import sys

from mcp.server.mcpserver import MCPServer

app = MCPServer("py-sdk-demo", version="0.1", instructions="Demo server for mcp-test-driver.")

@app.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b

@app.tool()
def echo(text: str) -> str:
    """Echo text back."""
    return text

@app.resource("demo://greeting")
def greeting() -> str:
    """A greeting."""
    return "hello from the SDK"

@app.prompt()
def review(code: str) -> str:
    """Ask for a code review."""
    return f"Please review:\n{code}"

if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    app.run(transport)
