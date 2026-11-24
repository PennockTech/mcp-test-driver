# Copyright 2026 Phil Pennock — see LICENSE file.

"""Builtins for the server's primitives: tools, resources, prompts, completion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..color import bold, dim, red, yellow
from ..helptext import annotation_tags, show_tool_help
from ..parse import parse_args
from ..render import render_result
from ..sanitize import sanitize, sanitize_any
from . import Arg, command, display_tool_name

if TYPE_CHECKING:
    from ..repl import Repl


# ── tools ──────────────────────────────────────────────────────────────


@command("/list", alias="/l", group="Tools", summary="List available tools")
def cmd_list(repl: Repl, rest: str) -> None:
    tools = repl.cache.ensure(repl.session, "tools")
    if not tools:
        print(dim("No tools available."))
        return
    for t in tools:
        raw = t.get("name")
        if not isinstance(raw, str):
            continue
        name = display_tool_name(raw)
        title = t.get("title")
        desc = sanitize_any(t.get("description", "")).split("\n", 1)[0]
        line = f"  {bold(name)}"
        if title:
            line += f" ({sanitize_any(title)})"
        tags = annotation_tags(t)
        if tags:
            line += " " + yellow("[" + ", ".join(tags) + "]")
        print(f"{line}: {dim(desc)}")


@command("/describe", alias="/d", group="Tools", arg=Arg.TOOL,
         summary="Show a tool's full definition", usage="/describe <tool>")
def cmd_describe(repl: Repl, rest: str) -> None:
    name = rest.strip()
    if not name:
        print(red("Usage: /describe <tool>"))
        return
    show_tool_help(repl.cache.completion, name)


# ── resources ──────────────────────────────────────────────────────────


def _print_resource_like(items: list[dict[str, Any]], uri_key: str) -> None:
    for r in items:
        uri = sanitize_any(r.get(uri_key, "?"))
        name = r.get("title") or r.get("name")
        mime = r.get("mimeType")
        line = f"  {bold(uri)}"
        if name:
            line += f"  {sanitize_any(name)}"
        if mime:
            line += f"  {dim('[' + sanitize_any(mime) + ']')}"
        if isinstance(r.get("size"), int):
            line += dim(f"  {r['size']} bytes")
        print(line)
        if r.get("description"):
            print(f"    {dim(sanitize_any(r['description']))}")


@command("/resources", alias="/lr", group="Resources", summary="List resources")
def cmd_resources(repl: Repl, rest: str) -> None:
    items = repl.cache.ensure(repl.session, "resources")
    if not items:
        print(dim("No resources available."))
        return
    _print_resource_like(items, "uri")


@command("/templates", alias="/lt", group="Resources", summary="List resource templates")
def cmd_templates(repl: Repl, rest: str) -> None:
    items = repl.cache.ensure(repl.session, "templates")
    if not items:
        print(dim("No resource templates available."))
        return
    _print_resource_like(items, "uriTemplate")


@command("/read", alias="/r", group="Resources", arg=Arg.RESOURCE,
         summary="Read a resource by URI", usage="/read <uri>")
def cmd_read(repl: Repl, rest: str) -> None:
    uri = rest.strip()
    if not uri:
        print(red("Usage: /read <uri>"))
        return
    repl.show(repl.session.read_resource(uri))


@command("/subscribe", alias="/sub", group="Resources", arg=Arg.RESOURCE,
         summary="Subscribe to resource updates", usage="/subscribe <uri>")
def cmd_subscribe(repl: Repl, rest: str) -> None:
    uri = rest.strip()
    if not uri:
        print(red("Usage: /subscribe <uri>"))
        return
    repl.session.subscribe_resource(uri)
    print(dim(f"Subscribed to {sanitize(uri)}."))


@command("/unsubscribe", alias="/unsub", group="Resources", arg=Arg.RESOURCE,
         summary="Unsubscribe from resource updates", usage="/unsubscribe <uri>")
def cmd_unsubscribe(repl: Repl, rest: str) -> None:
    uri = rest.strip()
    if not uri:
        print(red("Usage: /unsubscribe <uri>"))
        return
    repl.session.unsubscribe_resource(uri)
    print(dim(f"Unsubscribed from {sanitize(uri)}."))


# ── prompts ────────────────────────────────────────────────────────────


@command("/prompts", alias="/lp", group="Prompts", summary="List prompts")
def cmd_prompts(repl: Repl, rest: str) -> None:
    prompts = repl.cache.ensure(repl.session, "prompts")
    if not prompts:
        print(dim("No prompts available."))
        return
    for p in prompts:
        name = sanitize_any(p.get("name", "?"))
        title = p.get("title")
        desc = sanitize_any(p.get("description", ""))
        head = f"  {bold(name)}"
        if title:
            head += f" ({sanitize_any(title)})"
        print(f"{head}: {dim(desc)}")
        args = p.get("arguments", [])
        if isinstance(args, list):
            for arg in args:
                if not isinstance(arg, dict):
                    continue
                aname = sanitize_any(arg.get("name", "?"))
                adesc = sanitize_any(arg.get("description", ""))
                req = " (required)" if arg.get("required") else ""
                print(f"    {bold(aname + '=')}{req}  {dim(adesc)}")


def _string_args(args: dict[str, Any]) -> dict[str, str]:
    """Prompt arguments are strings in every revision; convert parsed values."""
    out = {}
    for k, v in args.items():
        if isinstance(v, str):
            out[k] = v
        elif isinstance(v, bool):
            out[k] = "true" if v else "false"
        elif isinstance(v, (int, float)):
            out[k] = str(v)
        else:
            out[k] = json.dumps(v, ensure_ascii=False)
    return out


@command("/prompt", alias="/p", group="Prompts", arg=Arg.PROMPT,
         summary="Get a prompt (with optional arguments)", usage="/prompt <name> [key=val ...]")
def cmd_prompt(repl: Repl, rest: str) -> None:
    parts = rest.strip().split(None, 1)
    if not parts:
        print(red("Usage: /prompt <name> [key=val ...]"))
        return
    name = parts[0]
    arguments = parse_args(parts[1]) if len(parts) > 1 else {}
    repl.show(repl.session.get_prompt(name, _string_args(arguments) or None))


# ── completion ─────────────────────────────────────────────────────────


@command(
    "/complete",
    alias="/c",
    group="Server",
    summary="Ask the server to complete a prompt or resource-template argument",
    usage=(
        "/complete prompt <name> <arg>=<partial> [ctx_key=val ...]\n"
        "/complete resource <uri-template> <arg>=<partial> [ctx_key=val ...]\n"
        "Extra key=val pairs are sent as context.arguments (2025-06-18+)."
    ),
    words=("prompt", "resource"),
)
def cmd_complete(repl: Repl, rest: str) -> None:
    parts = rest.split(None, 3)
    if len(parts) < 3 or parts[0] not in ("prompt", "resource") or "=" not in parts[2]:
        print(red("Usage: /complete prompt|resource <name-or-uri> <arg>=<partial> [ctx=val ...]"))
        return
    kind, target, argspec = parts[0], parts[1], parts[2]
    arg_name, _, partial = argspec.partition("=")
    ctx = _string_args(parse_args(parts[3])) if len(parts) > 3 else None
    ref = (
        {"type": "ref/prompt", "name": target}
        if kind == "prompt"
        else {"type": "ref/resource", "uri": target}
    )
    result = repl.session.complete(ref, arg_name, partial, ctx)
    completion = result.get("completion")
    if not isinstance(completion, dict):
        render_result(result)
        return
    values = completion.get("values")
    for v in values if isinstance(values, list) else []:
        print(f"  {sanitize_any(v)}")
    extra = []
    if "total" in completion:
        extra.append(f"total={sanitize_any(completion['total'])}")
    if completion.get("hasMore"):
        extra.append("more available")
    if extra:
        print(dim("  (" + ", ".join(extra) + ")"))
    if isinstance(values, list) and len(values) > 100:
        repl.session.findings.violation(
            "completion-too-many",
            f"completion/complete returned {len(values)} values; the spec caps it at 100",
            "server/utilities/completion",
        )


# ── utilities ──────────────────────────────────────────────────────────


@command("/ping", group="Server", summary="Ping the server")
def cmd_ping(repl: Repl, rest: str) -> None:
    repl.session.ping()
    print(dim("Pong."))


LOG_LEVELS = ("debug", "info", "notice", "warning", "error", "critical", "alert", "emergency")


@command(
    "/loglevel",
    alias="/ll",
    group="Server",
    arg=Arg.LEVEL,
    summary="Set the server log level",
    usage=(
        "/loglevel <level> [--rpc]\n"
        "Handshake era: sends logging/setLevel.  2026-07-28+: sets the per-request\n"
        "_meta log level instead, unless --rpc forces logging/setLevel.\n"
        "/loglevel off clears the per-request level."
    ),
)
def cmd_loglevel(repl: Repl, rest: str) -> None:
    words = rest.split()
    force_rpc = "--rpc" in words
    words = [w for w in words if w != "--rpc"]
    if not words:
        current = repl.session.log_level or "(none)"
        print(red("Usage: /loglevel <level> [--rpc]"))
        print(dim(f"  Levels: {', '.join(LOG_LEVELS)}.  Per-request level: {current}"))
        return
    level = words[0]
    if level not in LOG_LEVELS and level != "off":
        print(yellow(f"Note: {sanitize(level)!r} is not an RFC 5424 level; sending anyway."))
    if repl.session.state.era == "modern" and not force_rpc:
        repl.session.log_level = None if level == "off" else level
        print(dim(f"Per-request log level: {repl.session.log_level or 'off'}."))
        return
    repl.session.set_log_level(level)
    print(dim(f"Log level set to {sanitize(level)}."))


@command(
    "/roots",
    group="Server",
    summary="Show or change the roots capability",
    usage=(
        "/roots                show roots\n"
        "/roots on [path]      advertise path (default cwd); takes effect on /reconnect\n"
        "/roots off            stop advertising roots; takes effect on /reconnect\n"
        "/roots add <path>     add a root under the base path, then notify the server\n"
        "/roots remove <uri>   remove a root, then notify the server"
    ),
    words=("on", "off", "add", "remove"),
)
def cmd_roots(repl: Repl, rest: str) -> None:
    session = repl.session
    parts = rest.split(None, 1)
    sub = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    handler = session.roots_handler
    if not sub:
        if handler is None:
            print(dim("Roots: disabled"))
            print(dim("  Use /roots on [path] to enable, then /reconnect."))
            return
        print(bold("Roots: enabled"))
        print(f"  Base: {handler.base}")
        for r in handler.roots:
            print(f"  {dim(r['uri'])}  {r.get('name', '')}")
        return
    if sub == "on":
        try:
            session.enable_roots(Path(arg) if arg else Path.cwd())
        except (OSError, ValueError) as e:
            print(red(f"Cannot set roots: {e}"))
            return
        print(dim(f"Roots enabled for {session.roots_handler.base if session.roots_handler else '?'}."))
        print(dim("Run /reconnect for the server to see this."))
    elif sub == "off":
        session.disable_roots()
        print(dim("Roots disabled.  Run /reconnect for the server to see this."))
    elif sub in ("add", "remove"):
        if handler is None:
            print(red("Roots are not enabled; use /roots on first."))
            return
        if not arg:
            print(red(f"Usage: /roots {sub} <{'path' if sub == 'add' else 'uri'}>"))
            return
        try:
            if sub == "add":
                handler.add_root(Path(arg))
            elif not handler.remove_root(arg):
                print(red(f"No root {sanitize(arg)}"))
                return
        except (OSError, ValueError) as e:
            print(red(f"Cannot {sub} root: {e}"))
            return
        if session.roots_list_changed():
            print(dim("Sent notifications/roots/list_changed."))
        else:
            print(dim("Roots updated; the server learns of it on its next roots/list."))
    else:
        print(red("Usage: /roots [on [path] | off | add <path> | remove <uri>]"))
