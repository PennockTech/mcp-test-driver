# Copyright 2026 Phil Pennock — see LICENSE file.

"""Builtins for extensions: tasks, MCP Apps, skills, and declaring extensions."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..color import bold, dim, red, yellow
from ..diagnostics import Level, print_findings
from ..messages import as_obj
from ..parse import parse_args
from ..render import render_result
from ..sanitize import safe_json, sanitize, sanitize_any
from ..tasks import TASKS_EXTENSION, print_task
from ..versions import Feature
from . import Arg, command

if TYPE_CHECKING:
    from ..repl import Repl

APPS_EXTENSION = "io.modelcontextprotocol/ui"
SKILLS_EXTENSION = "io.modelcontextprotocol/skills"
APP_MIME = "text/html;profile=mcp-app"


# ── declaring extensions ───────────────────────────────────────────────


@command(
    "/extension",
    group="Client",
    summary="Show extensions both sides declare, or declare one ourselves",
    usage="/extension                    show\n/extension add ID [{settings}]  declare (off by default)\n"
          "/extension remove ID",
    words=("add", "remove"),
)
def cmd_extension(repl: Repl, rest: str) -> None:
    session = repl.session
    words = rest.split(None, 2)
    if words and words[0] == "add" and len(words) > 1:
        settings = parse_args(words[2]) if len(words) > 2 else {}
        session.extensions[words[1]] = settings
        print(dim(f"Declared {sanitize(words[1])}.  Handshake-era servers see it after /reconnect."))
    elif words and words[0] == "remove" and len(words) > 1:
        session.extensions.pop(words[1], None)
    elif words:
        print(red("Usage: /extension [add ID [settings] | remove ID]"))
        return
    server_ext = as_obj(session.server_capabilities.get("extensions"))
    print(bold("Server extensions:"))
    for k, v in server_ext.items():
        print(f"  {sanitize_any(k)}  {dim(safe_json(v, indent=None))}")
    if not server_ext:
        print(dim("  (none)"))
    print(bold("Client extensions:"))
    for k, v in session.extensions.items():
        print(f"  {sanitize(k)}  {dim(safe_json(v, indent=None))}")
    if not session.extensions:
        print(dim("  (none)"))


# ── tasks ──────────────────────────────────────────────────────────────


@command("/task", group="Tools", arg=Arg.TOOL,
         summary="Call a tool as a task (2025-11-25 params.task, or the 2026 tasks extension)",
         usage="/task <tool> [key=val ...]")
def cmd_task(repl: Repl, rest: str) -> None:
    parts = rest.strip().split(None, 1)
    if not parts:
        print(red("Usage: /task <tool> [args]"))
        return
    state = repl.cache.completion
    name = state.real_tool_name(parts[0])
    arguments = parse_args(parts[1]) if len(parts) > 1 else {}
    session = repl.session
    params: dict[str, Any] = {"name": name, "arguments": arguments}
    if session.spec.has(Feature.TASKS_CORE) or session.state.era == "handshake":
        params["task"] = {}
    elif TASKS_EXTENSION not in session.extensions:
        session.extensions[TASKS_EXTENSION] = {}
        print(dim(f"(declaring {TASKS_EXTENSION} for this and later requests)"))
    repl.show_call(session.send_request("tools/call", params))


@command(
    "/tasks",
    group="Tools",
    summary="Inspect and manage tasks",
    usage=(
        "/tasks list                 (2025-11-25 core tasks)\n"
        "/tasks get <id>\n"
        "/tasks wait <id>            follow until done\n"
        "/tasks result <id>          (2025-11-25: the outcome; blocks until done)\n"
        "/tasks update <id> {json}   (extension: inputResponses)\n"
        "/tasks cancel <id>"
    ),
    words=("list", "get", "wait", "result", "update", "cancel"),
)
def cmd_tasks(repl: Repl, rest: str) -> None:
    from ..tasks import TaskHandle, follow

    words = rest.split(None, 2)
    sub = words[0] if words else "list"
    session = repl.session
    if sub == "list":
        result = session.call("tasks/list")
        tasks = result.get("tasks")
        for t in tasks if isinstance(tasks, list) else []:
            print_task(as_obj(t))
        if not tasks:
            print(dim("No tasks."))
        return
    if len(words) < 2:
        print(red(f"Usage: /tasks {sub} <taskId>"))
        return
    task_id = words[1]
    if sub == "get":
        print_task(session.call("tasks/get", {"taskId": task_id}))
    elif sub == "wait":
        task = session.call("tasks/get", {"taskId": task_id})
        gen = "core" if session.spec.has(Feature.TASKS_CORE) else "extension"
        repl.show(follow(session, TaskHandle(task_id, gen, task)))
    elif sub == "result":
        repl.show(session.send_request("tasks/result", {"taskId": task_id}))
    elif sub == "update":
        responses = parse_args(words[2]) if len(words) > 2 else {}
        session.call("tasks/update", {"taskId": task_id, "inputResponses": responses})
        print(dim("Sent."))
    elif sub == "cancel":
        result = session.call("tasks/cancel", {"taskId": task_id})
        if result:
            print_task(result)
        else:
            print(dim("Cancellation requested."))
    else:
        print(red(f"Unknown /tasks subcommand {sanitize(sub)!r}"))


# ── MCP Apps (SEP-1865) ────────────────────────────────────────────────


def _app_uri(tool: dict[str, Any]) -> str | None:
    uri = as_obj(as_obj(tool.get("_meta")).get("ui")).get("resourceUri")
    return uri if isinstance(uri, str) else None


@command(
    "/app",
    group="Resources",
    summary="MCP Apps: list tools with UI resources, or save one to a file",
    usage="/app                   list tools with ui:// resources\n"
          "/app save <tool|uri> <file>   write the HTML to a file (it is untrusted)",
    words=("save",),
)
def cmd_app(repl: Repl, rest: str) -> None:
    words = rest.split()
    tools = repl.cache.ensure(repl.session, "tools")
    if not words:
        found = False
        for t in tools:
            uri = _app_uri(t)
            if uri:
                found = True
                print(f"  {bold(sanitize_any(t.get('name')))} → {sanitize(uri)}")
        for r in repl.cache.lists.get("resources", []):
            if str(r.get("mimeType", "")).startswith(APP_MIME):
                found = True
                print(f"  {dim('resource')} {sanitize_any(r.get('uri'))}")
        if not found:
            print(dim("No MCP App resources."))
        return
    if words[0] != "save" or len(words) != 3:
        print(red("Usage: /app save <tool|uri> <file>"))
        return
    target, path = words[1], Path(words[2])
    real = repl.cache.completion.real_tool_name(target)
    uri = next((u for t in tools if t.get("name") == real and (u := _app_uri(t))), target)
    result = repl.session.call("resources/read", {"uri": uri})
    contents = [as_obj(c) for c in result.get("contents", []) if isinstance(c, dict)]
    html = next((c for c in contents if isinstance(c.get("text"), str)), None)
    if html is None:
        print(red(f"No text content at {sanitize(uri)}"))
        return
    if not str(html.get("mimeType", "")).startswith(APP_MIME):
        repl.session.findings.violation("app-mime", f"{sanitize(uri)} is not {APP_MIME}", "SEP-1865",
                                        level=Level.WARNING)
    path.write_text(html["text"])
    print(dim(f"Wrote {len(html['text'])} characters to {path}.  It is server-supplied HTML: "
              "open it in a sandboxed browser profile."))
    ui = as_obj(html.get("_meta")).get("ui")
    if ui:
        print(dim(f"  _meta.ui: {safe_json(ui, indent=None)}"))


# ── skills (SEP-2640) ──────────────────────────────────────────────────


def _digest_ok(expected: str, content: dict[str, Any]) -> bool | None:
    algo, _, want = expected.partition(":")
    if algo != "sha256":
        return None
    if isinstance(content.get("text"), str):
        data = content["text"].encode()
    elif isinstance(content.get("blob"), str):
        data = base64.b64decode(content["blob"])
    else:
        return None
    return hashlib.sha256(data).hexdigest() == want


@command(
    "/skills",
    group="Resources",
    summary="Skills extension: list, describe and read skills (verifying digests)",
    usage="/skills                list (skills/list)\n/skills get <uri>      (skills/get)\n"
          "/skills read <uri>     read a skill file, checking its listed digest",
    words=("get", "read"),
)
def cmd_skills(repl: Repl, rest: str) -> None:
    words = rest.split()
    session = repl.session
    if not words:
        result = session.call("skills/list")
        skills = [as_obj(s) for s in result.get("skills", []) if isinstance(s, dict)]
        repl.skills = {str(s.get("uri")): s for s in skills}
        for s in skills:
            fm = as_obj(s.get("frontmatter"))
            files = s.get("resources")
            n = len(files) if isinstance(files, list) else sanitize_any(files)
            print(f"  {bold(sanitize_any(fm.get('name', '?')))}  {dim(sanitize_any(s.get('uri')))}  ({n} file(s))")
            if fm.get("description"):
                print(f"    {dim(sanitize_any(fm['description']))}")
        if not skills:
            print(dim("No skills listed."))
        return
    if len(words) != 2 or words[0] not in ("get", "read"):
        print(red("Usage: /skills [get <uri> | read <uri>]"))
        return
    uri = words[1]
    if words[0] == "get":
        render_result(session.call("skills/get", {"uri": uri}))
        return
    result = session.call("resources/read", {"uri": uri})
    render_result(result)
    listed = {str(f.get("uri")): f for s in repl.skills.values() for f in s.get("resources", []) if isinstance(f, dict)}
    entry = listed.get(uri)
    if entry is None:
        print(yellow("Not listed by /skills, so its digest cannot be checked."))
        return
    for content in result.get("contents", []):
        ok = _digest_ok(str(entry.get("digest", "")), as_obj(content))
        if ok is False:
            session.findings.violation("skill-digest-mismatch",
                                       f"{sanitize(uri)} does not match the digest skills/list gave",
                                       "SEP-2640")
        elif ok:
            print(dim("  digest verified"))
    print_findings(session.findings.take_unshown())
