---
name: add-command
description: Add or change a builtin REPL /command in mcp-test-driver (registry entry, handler, help, tab-completion, tests). Use when asked for a new slash command or a new subcommand of one.
---

# Add a builtin `/command`

One registry (`src/mcp_test_driver/commands/__init__.py`) drives dispatch,
`/help` and tab-completion.  Registering the command is all the wiring.

1. **Pick the module** under `commands/` by area: `server.py` (tools,
   resources, prompts, completion), `client.py` (capabilities we declare),
   `protocol_cmds.py` (raw access), `streams.py` (discover, listen),
   `diag.py` (report, findings, probes), `web.py` (auth, server cards),
   `ext.py` (tasks, apps, skills, extensions), `core.py` (session).
   A new module must be added to the import at the bottom of
   `commands/__init__.py`.

2. **Register the handler:**

   ```python
   @command("/name", alias="/n", group="Server", arg=Arg.TOOL,
            summary="One line for /help", usage="/name <tool> [key=val ...]",
            words=("on", "off"))
   def cmd_name(repl: Repl, rest: str) -> None:
       ...
   ```

   - `group` is one of `GROUPS`; `arg` says what the first argument is,
     for tab-completion; `words` are literal first arguments to complete.
   - Return `False` only to end the REPL.
   - The name and alias must be unused; registration raises on a clash.

3. **Follow the rules:**
   - Print server-supplied data only through `repl.show()`, `render.py`,
     `sanitize()` or `safe_json()`.
   - Never refuse because the negotiated revision lacks the method.  Send
     it; the session records a note.  Use `repl.session.spec` only to
     choose a default mechanism.
   - Call the session (`repl.session.call(...)` or a typed method on
     `McpSession`); do not build JSON-RPC IDs yourself.
   - Fetch lists through `repl.cache`, so unadvertised lists are fetched
     on demand with a note.

4. **Test** in the file for that area (`tests/test_repl.py`,
   `tests/test_extensions.py`, ...): build a REPL on a fake server with
   the `make_repl` fixture pattern, call `r.handle_line("/name ...")`,
   and assert on `capsys` output and on what the fake logged
   (`read_log(log_path)`).  If the server side is new, add a scenario key
   to `tests/fakes/engine.py` and document it in the docstring.

5. **Docs:** `/help` is the command reference; nothing else lists
   commands.  Mention the command in `docs/user-guide.md` only if it
   introduces a capability worth a sentence there, and update the Driver
   column of `docs/mcp-feature-matrix.md` if it adds support for a spec
   feature.

6. Try it (`try-driver` skill), then run the gate: `uv run ruff check`,
   `uv run ty check`, `uv run pytest`.
