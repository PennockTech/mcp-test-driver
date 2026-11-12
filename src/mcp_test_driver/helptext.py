# Copyright 2026 Phil Pennock — see LICENSE file.

"""Help output: general help, per-command help, per-tool help."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import commands
from .color import bold, dim, green, red, yellow
from .messages import as_obj
from .sanitize import safe_json, sanitize, sanitize_any

if TYPE_CHECKING:
    from .completion import CompletionState


def show_general_help() -> None:
    print()
    for group, cmds in commands.by_group():
        print(f"  {group}:")
        for c in cmds:
            alias = c.alias or ""
            print(f"    {bold(c.name):24s} {alias:7s} {dim(c.summary)}")
        print()
    print("  Tool invocation:")
    rows = [
        ("<tool>", "call with no arguments"),
        ("<tool> key=val ...", "call with keyword arguments"),
        ("<tool> key=[a b] ...", "list value"),
        ("<tool> key={k: v}", "dict value"),
        ("<tool> {...}", "call with a raw JSON object"),
    ]
    for left, right in rows:
        print(f"    {bold(left):<24s}         {right}")
    print()
    print("  Key=value notes:")
    print("    Booleans: exact=true  exact=false")
    print("    Integers: decimal=10003  (parsed automatically)")
    print("    Strings:  all other values; quote with shell rules if they contain spaces")
    print('    Lists:    paths=[src/foo.py src/bar.py]  tags=["hello world", foo, 42]')
    print("    Dicts:    filter={type: file, recursive: true}")
    print()
    print(f"  Press {bold('Tab')} to complete, {bold('F1')}/{bold('Esc-h')} for help.")
    print()


def show_command_help(cmd: commands.Command) -> None:
    alias = f" ({cmd.alias})" if cmd.alias else ""
    print(f"\n  {bold(cmd.name)}{alias}: {cmd.summary}")
    if cmd.usage != cmd.name:
        for line in cmd.usage.splitlines():
            print(f"    {dim(line)}")
    print()


# (annotation key, label when true, label when false)
_HINTS = (
    ("readOnlyHint", "read-only", "writes"),
    ("destructiveHint", "destructive", "non-destructive"),
    ("idempotentHint", "idempotent", "non-idempotent"),
    ("openWorldHint", "open-world", "closed-world"),
)


def annotation_tags(tool: dict[str, Any]) -> list[str]:
    """Short tags for the behaviour hints a tool states (2025-03-26+).

    Only hints the server actually sent are shown; spec defaults are not
    filled in, so a developer sees exactly what their server declares.
    """
    ann = tool.get("annotations")
    if not isinstance(ann, dict):
        return []
    tags = []
    for key, yes, no in _HINTS:
        value = ann.get(key)
        if value is True:
            tags.append(yes)
        elif value is False:
            tags.append(no)
    return tags


def show_tool_help(state: CompletionState, name: str) -> None:
    if name not in state.tool_names:
        print(f"\n  {red('Unknown tool:')} {sanitize(name)}")
        return
    tool = state.tool_defs.get(name, {})
    title = tool.get("title") or (tool.get("annotations") or {}).get("title")
    head = f"\n  {bold(name)}"
    real = state.real_tool_name(name)
    if real != name:
        head += dim(f"  (server name {sanitize_any(repr(real))})")
    if title:
        head += f"  {sanitize_any(title)}"
    print(head)
    desc = state.tool_descriptions.get(name, "")
    if desc:
        for line in desc.splitlines():
            print(f"    {line}")
    tags = annotation_tags(tool)
    if tags:
        print(f"    {yellow('[' + ', '.join(tags) + ']')}")
    schema = state.tool_schemas.get(name, {})
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    if isinstance(props, dict) and props:
        print("  Arguments:")
        for k, raw in sorted(props.items()):
            k = sanitize(str(k))
            v = as_obj(raw)
            req = " (required)" if k in required else ""
            typ = sanitize_any(v.get("type", ""))
            d = sanitize_any(v.get("description", ""))
            line = f"    {bold(k + '='):<24s} {dim(typ)}{req}"
            if d:
                line += f"  {d}"
            print(line)
            enum = state.arg_enums.get(f"{name}:{k}")
            if enum:
                print(f"      values: {', '.join(enum)}")
            if "default" in v:
                print(f"      default: {safe_json(v['default'], indent=None)}")
    output = tool.get("outputSchema")
    if output is not None:
        print(f"  {green('Output schema:')}")
        for line in safe_json(output).splitlines():
            print(f"    {line}")
    execution = tool.get("execution")
    if isinstance(execution, dict) and execution.get("taskSupport"):
        print(f"  Task support: {sanitize_any(execution['taskSupport'])}")
    icons = tool.get("icons")
    if isinstance(icons, list) and icons:
        print(dim(f"  Icons: {len(icons)}"))
    meta = tool.get("_meta")
    if isinstance(meta, dict) and meta:
        print(dim(f"  _meta: {safe_json(meta, indent=None)}"))
    print()


def show_context_help_for(state: CompletionState, text: str) -> None:
    """Help for a partial input line (the F1 / Esc-h macro, and /help <x>)."""
    parts = text.strip().split()
    if not parts:
        show_general_help()
        return
    first = parts[0]
    cmd = commands.resolve(first)
    if cmd is not None:
        if cmd.arg == commands.Arg.TOOL and len(parts) > 1:
            show_tool_help(state, parts[1])
        elif cmd.name == "/help" and len(parts) > 1:
            show_context_help_for(state, " ".join(parts[1:]))
        else:
            show_command_help(cmd)
        return
    if first in state.tool_names:
        if len(parts) > 1 and "=" in parts[-1]:
            key = parts[-1].partition("=")[0]
            desc = state.arg_descriptions.get(f"{first}:{key}")
            if desc:
                print(f"\n  {bold(key)}: {desc}")
                return
        show_tool_help(state, first)
        return
    print(f"\n  {red('Unknown:')} {sanitize(first)}", dim("(type '/list' to see tools)"))
