# 0004: Server-supplied text is untrusted; builtins own the `/` prefix

Status: Accepted (2026-03-31)

## Context

The tool exists to be pointed at servers under development and at
strangers' servers.  Tool names, descriptions, results, instructions,
URLs, error messages and stderr all reach a terminal, where escape
sequences can retitle the window, write the clipboard, hide text or
reorder it.  A tool named like a builtin could take over `quit` or `help`.

## Decision

- Every string from a server goes through `sanitize.sanitize()`, or
  `sanitize.safe_json()` for JSON, before it is printed or handed to
  readline.  There is no trusted server field.
- Builtin commands start with `/`.  `display_tool_name()` strips leading
  `/` from tool names, so no tool can be typed as a builtin.  The
  server's real name is kept and used for the call.

## Consequences

- Never `print()` or interpolate raw server data, error paths included.
  New rendering goes in `render.py` or uses the two functions.
- Sanitize after JSON encoding, not before: see `safe_json()`.
- Tests: `tests/test_security.py`.  Threat table:
  [../designs/security.md](../designs/security.md).
