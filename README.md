# mcp-test-driver

An interactive REPL and diagnostic driver for any
[MCP](https://modelcontextprotocol.io/) server, over stdio or HTTP.  No AI
wiring: it is a tool for developers building and maintaining MCP servers.

Point it at a command or a URL.  You get a prompt where tool names,
argument keys and enum values tab-complete, a report of what the server
negotiated and where it departs from the spec, and raw protocol access
for everything else.

## Installation

mcp-test-driver is on [PyPI](https://pypi.org/project/mcp-test-driver/) and
needs Python 3.12 or later.

```sh
uvx mcp-test-driver <server-command...>   # run without installing
uv tool install mcp-test-driver           # or install the command
pipx install mcp-test-driver
```

## Quick start

```sh
# stdio: the server command and its arguments
mcp-test-driver my-server --stdio

# HTTP: a URL
mcp-test-driver https://unicode.mcp.pennock.tech/mcp

# a token from the environment, kept out of argv
TOKEN=... mcp-test-driver --bearer-env TOKEN https://example.com/mcp
```

Options go before the target; everything after it is passed to the server
command unchanged.  At the prompt, type a tool name and its arguments, or
a `/command`:

```
mcp> unicode_search query=snowman
mcp> /describe unicode_search
mcp> /help
```

**Tab** completes; **F1** shows help for whatever is on the line.

## What it does

- **Speaks every MCP revision**, 2024-11-05 to 2026-07-28: the
  `initialize` handshake and the stateless protocol, over stdio,
  Streamable HTTP and the 2024-11-05 HTTP+SSE.  It works out which a server
  speaks and tells you how it decided.
- **Never hides a feature.**  The negotiated revision sets defaults and
  labels findings; you can always send anything, including methods the
  server never claimed to support.
- **Reports conformance problems as you work**, labelled as violations,
  features beyond the claimed revision, or deprecated ones.
- **Probes and checks a server on request**: what it really supports,
  and how it handles bad input.  Probes never call the server's own
  tools.
- **Plays the client's side by hand**: you answer elicitation and
  sampling requests yourself, and can advertise roots.
- **Handles tasks, MCP Apps and skills**, and lets you declare any
  extension.
- **Gives raw access**: send any request, notification or deliberately
  invalid payload; set sticky `_meta` fields and HTTP headers.
- **Reaches real deployments**: OAuth login, static tokens, HTTP
  proxies, extra CA certificates.
- **Treats the server as untrusted**: everything it sends is sanitized
  before it reaches your terminal, and URLs it supplies are never opened
  for you.

## Scripting and CI

```sh
mcp-test-driver --check --json my-server      # exit 1 on failures, 2 if unreachable
mcp-test-driver -c '/probe' -c 'my_tool x=1' my-server
```

## Documentation

- `mcp-test-driver --help` lists every option; `/help` lists every
  command.
- [User guide](https://github.com/PennockTech/mcp-test-driver/blob/main/docs/user-guide.md):
  protocol eras, findings, probes, client capabilities, proxies and TLS,
  OAuth.
- [Feature matrix](https://github.com/PennockTech/mcp-test-driver/blob/main/docs/mcp-feature-matrix.md):
  each spec feature, by revision, and what the driver does with it.
- [Security model](https://github.com/PennockTech/mcp-test-driver/blob/main/docs/designs/security.md)
  and [what is not implemented](https://github.com/PennockTech/mcp-test-driver/blob/main/docs/roadmap.md).

## Development

```sh
uv sync --group dev
uv run ruff check && uv run ty check && uv run pytest
```

Start from [AGENTS.md](https://github.com/PennockTech/mcp-test-driver/blob/main/AGENTS.md)
and the [docs index](https://github.com/PennockTech/mcp-test-driver/blob/main/docs/README.md);
both are written for AI coding agents and serve people as well.

## AI Disclosure

A LLM development tool was used extensively in the creation of this tool.

(It's a tool for exploring services designed for LLMs to use, what did you
expect?)

## License

ISC — see [LICENSE](https://github.com/PennockTech/mcp-test-driver/blob/main/LICENSE).
