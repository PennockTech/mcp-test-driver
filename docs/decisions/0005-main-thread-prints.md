# 0005: Only the main thread prints

Status: Accepted (2026-11-12)

## Context

Stdio stderr, SSE response streams, the GET listening stream and the
HTTP+SSE stream all deliver data while the user sits at a readline
prompt.  Output written from another thread lands inside the prompt line
and corrupts it.

## Decision

A reader thread parses and puts items on a queue, nothing more.  The main
thread drains the queues, during a request and before each prompt, and
does all printing, sanitizing first.

## Consequences

- No `print()`, `eprint()` or findings output from a thread.
- A new background source needs a queue and a drain point (`poll()` on
  the transport, called from `McpSession.pump()`).
- Idle-time messages appear at the next prompt, not the moment they
  arrive.  `/listen` exists for watching live.
