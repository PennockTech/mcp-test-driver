# 0015: No LLM is wired in; the person at the terminal answers

Status: Accepted (2026-11-12)

## Context

MCP servers are built for LLM clients, and can ask the client for things:
roots, elicitation, sampling.  This tool is for developers debugging
servers.  Wiring in a model would add credentials, cost and
non-determinism, and hide what the server sent.

## Decision

- The driver is not an LLM client and calls no model.
- Client capabilities are off by default and declared only when enabled
  (`--roots`, `--elicitation`, `--sampling`, `--extension`), so a server
  sees exactly what the user chose.
- Sampling is answered by the user, who plays the model.  Elicitation is
  answered at the prompt.  `--elicitation-auto` (schema defaults) exists
  for scripted runs.
- Non-interactive modes (`--check`, `--probe`, `-c`, `--json`) cover CI.

## Consequences

- Requests for an undeclared capability get "method not supported".
- Features that need a model's judgement are out of scope.
