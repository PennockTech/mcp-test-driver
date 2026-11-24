# Copyright 2026 Phil Pennock — see LICENSE file.

"""Interactive client-side handlers: elicitation and human-in-the-loop sampling.

These answer server requests by asking the person at the terminal.  The
server's message, schema, titles and URLs are all untrusted and shown only
through sanitize(); URLs are never opened automatically.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlsplit

from .color import bold, cyan, dim, red, yellow
from .handlers import HandlerError
from .messages import as_obj
from .render import render_block
from .sanitize import safe_json, sanitize, sanitize_any


class Prompter(Protocol):
    """Where interactive handlers get answers; swapped out in tests."""

    def ask(self, prompt: str) -> str: ...


class ConsolePrompter:
    """Reads from the terminal with tab-completion switched off."""

    def __init__(self, suspend: Callable[[], Any] | None = None) -> None:
        self._suspend = suspend

    def ask(self, prompt: str) -> str:
        if self._suspend is None:
            return input(prompt)
        with self._suspend():
            return input(prompt)


class _Abort(Exception):
    def __init__(self, action: str) -> None:
        self.action = action


_ESCAPES = {"!decline": "decline", "!cancel": "cancel"}


# ── elicitation ────────────────────────────────────────────────────────


def _choices(prop: dict[str, Any]) -> tuple[list[str], list[str], bool] | None:
    """(values, labels, multi) for the enum forms of 2025-06-18 and SEP-1330."""
    if prop.get("type") == "array":
        items = as_obj(prop.get("items"))
        inner = _choices(items) if items else None
        if inner is None:
            alts = items.get("anyOf")
            if isinstance(alts, list) and all(isinstance(a, dict) and "const" in a for a in alts):
                inner = ([str(a["const"]) for a in alts], [str(a.get("title", a["const"])) for a in alts], False)
        return (inner[0], inner[1], True) if inner else None
    if isinstance(prop.get("enum"), list):
        values = [str(v) for v in prop["enum"]]
        names = prop.get("enumNames")
        labels = [str(n) for n in names] if isinstance(names, list) and len(names) == len(values) else values
        return values, labels, False
    alts = prop.get("oneOf")
    if isinstance(alts, list) and alts and all(isinstance(a, dict) and "const" in a for a in alts):
        return [str(a["const"]) for a in alts], [str(a.get("title", a["const"])) for a in alts], False
    return None


def _coerce(prop: dict[str, Any], raw: str) -> Any:
    ptype = prop.get("type")
    if ptype == "boolean":
        low = raw.strip().lower()
        if low in ("y", "yes", "true", "1"):
            return True
        if low in ("n", "no", "false", "0"):
            return False
        raise ValueError("answer yes or no")
    if ptype == "integer":
        value: Any = int(raw)
    elif ptype == "number":
        value = float(raw)
    else:
        value = raw
        if isinstance(prop.get("minLength"), int) and len(raw) < prop["minLength"]:
            raise ValueError(f"at least {prop['minLength']} characters")
        if isinstance(prop.get("maxLength"), int) and len(raw) > prop["maxLength"]:
            raise ValueError(f"at most {prop['maxLength']} characters")
        return value
    if isinstance(prop.get("minimum"), (int, float)) and value < prop["minimum"]:
        raise ValueError(f"minimum is {prop['minimum']}")
    if isinstance(prop.get("maximum"), (int, float)) and value > prop["maximum"]:
        raise ValueError(f"maximum is {prop['maximum']}")
    return value


class ElicitationHandler:
    """Answers elicitation/create by asking the user.

    Form mode walks the requested schema field by field; URL mode shows the
    URL (and its host, prominently) and asks for consent.  At any prompt,
    "!decline" or "!cancel" ends the elicitation with that action.
    """

    def __init__(self, prompter: Prompter, modes: frozenset[str] = frozenset({"form"})) -> None:
        self.prompter = prompter
        self.modes = modes

    @property
    def capability(self) -> tuple[str, dict[str, Any]]:
        # An empty object means form-only in every revision since 2025-06-18;
        # URL mode (2025-11-25) must be declared explicitly.
        if "url" in self.modes:
            return ("elicitation", {"form": {}, "url": {}})
        return ("elicitation", {})

    def _ask(self, prompt: str) -> str:
        answer = self.prompter.ask(prompt)
        if answer.strip() in _ESCAPES:
            raise _Abort(_ESCAPES[answer.strip()])
        return answer

    def handle(self, params: dict[str, Any]) -> dict[str, Any]:
        mode = params.get("mode", "form")
        if mode not in self.modes:
            raise HandlerError(-32602, f"Elicitation mode {mode!r} was not declared by this client")
        print()
        print(bold(cyan("── The server is asking for input (elicitation) ──")))
        message = params.get("message")
        if isinstance(message, str):
            print(f"  {sanitize(message)}")
        try:
            if mode == "url":
                return self._url(params)
            return self._form(params)
        except _Abort as a:
            print(dim(f"  ({a.action}d)"))
            return {"action": a.action}
        except EOFError:
            print(dim("  (cancelled)"))
            return {"action": "cancel"}

    def _url(self, params: dict[str, Any]) -> dict[str, Any]:
        url = sanitize_any(params.get("url", ""))
        host = urlsplit(url).hostname or "?"
        print(f"  URL:  {bold(url)}")
        print(f"  Host: {yellow(host)}")
        if params.get("elicitationId"):
            print(dim(f"  elicitationId: {sanitize_any(params['elicitationId'])}"))
        print(dim("  Open it yourself if you trust it; this tool never opens URLs."))
        answer = self._ask("  Accept [a], decline [d] or cancel [c]? ").strip().lower()
        action = {"a": "accept", "d": "decline", "c": "cancel"}.get(answer[:1], "decline")
        return {"action": action}

    def _form(self, params: dict[str, Any]) -> dict[str, Any]:
        schema = as_obj(params.get("requestedSchema"))
        props = as_obj(schema.get("properties"))
        req = schema.get("required")
        required: list[Any] = req if isinstance(req, list) else []
        answer = self._ask("  Respond [a], decline [d] or cancel [c]? ").strip().lower()
        if answer[:1] == "d":
            return {"action": "decline"}
        if answer[:1] == "c":
            return {"action": "cancel"}
        content: dict[str, Any] = {}
        for name, raw_prop in props.items():
            prop = as_obj(raw_prop)
            value = self._field(str(name), prop, name in required)
            if value is not None:
                content[str(name)] = value
        return {"action": "accept", "content": content}

    def _field(self, name: str, prop: dict[str, Any], required: bool) -> Any:
        title = sanitize_any(prop.get("title", name))
        desc = prop.get("description")
        print(f"  {bold(title)}{red(' *') if required else ''}" + (f"  {dim(sanitize_any(desc))}" if desc else ""))
        choices = _choices(prop)
        default = prop.get("default")
        if choices:
            values, labels, multi = choices
            for i, (v, label) in enumerate(zip(values, labels, strict=True), 1):
                extra = f" ({sanitize(v)})" if label != v else ""
                print(f"    {i}. {sanitize(label)}{extra}")
        hint = []
        if prop.get("type"):
            hint.append(sanitize_any(prop["type"]))
        if prop.get("format"):
            hint.append(sanitize_any(prop["format"]))
        if default is not None:
            hint.append(f"default {safe_json(default, indent=None)}")
        prompt = f"    {name}{' [' + ', '.join(hint) + ']' if hint else ''}: "
        while True:
            raw = self._ask(prompt).strip()
            if not raw:
                if default is not None:
                    return default
                if not required:
                    return None
                print(red("    required"))
                continue
            try:
                if choices:
                    return self._pick(choices, raw)
                return _coerce(prop, raw)
            except ValueError as e:
                print(red(f"    {e}"))

    @staticmethod
    def _pick(choices: tuple[list[str], list[str], bool], raw: str) -> Any:
        values, _labels, multi = choices
        picks = [p.strip() for p in raw.split(",")] if multi else [raw]
        out = []
        for p in picks:
            if p.isdigit() and 1 <= int(p) <= len(values):
                out.append(values[int(p) - 1])
            elif p in values:
                out.append(p)
            else:
                raise ValueError(f"choose from 1-{len(values)}")
        return out if multi else out[0]


def default_value(prop: dict[str, Any]) -> Any:
    """A schema's default, else a plain value of the right type."""
    if "default" in prop:
        return prop["default"]
    choices = _choices(prop)
    if choices:
        values, _labels, multi = choices
        return values[:1] if multi else values[0]
    return {"boolean": True, "integer": 0, "number": 0, "array": []}.get(str(prop.get("type")), "")


class AutoElicitationHandler:
    """Accepts every form elicitation with the schema's defaults, unprompted.

    For scripted runs (--elicitation-auto, --exec, conformance testing):
    fields with a default get it, required fields without one get a plain
    value of their type, optional ones are left out.
    """

    capability = ("elicitation", {"form": {}})

    def handle(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("mode", "form") != "form":
            raise HandlerError(-32602, "Only form-mode elicitation is answered automatically")
        schema = as_obj(params.get("requestedSchema"))
        req = schema.get("required")
        required = req if isinstance(req, list) else []
        content = {}
        for name, raw in as_obj(schema.get("properties")).items():
            prop = as_obj(raw)
            if "default" in prop or name in required:
                content[str(name)] = default_value(prop)
        print(dim(f"  (elicitation answered automatically: {safe_json(content, indent=None)})"))
        return {"action": "accept", "content": content}


# ── sampling ───────────────────────────────────────────────────────────


class SamplingHandler:
    """Answers sampling/createMessage: the user plays the language model.

    Type the assistant's reply as one line, "!json {...}" for a complete
    CreateMessageResult (e.g. to answer with tool_use), or "!decline".
    """

    capability = ("sampling", {})

    def __init__(self, prompter: Prompter) -> None:
        self.prompter = prompter

    def handle(self, params: dict[str, Any]) -> dict[str, Any]:
        print()
        print(bold(cyan("── The server asks you to act as its LLM (sampling) ──")))
        if isinstance(params.get("systemPrompt"), str):
            print(dim(f"  system: {sanitize(params['systemPrompt'])}"))
        messages = params.get("messages")
        for msg in messages if isinstance(messages, list) else []:
            m = as_obj(msg)
            print(bold(f"  [{sanitize_any(m.get('role', '?'))}]"))
            content = m.get("content")
            for block in content if isinstance(content, list) else [content]:
                render_block(block, 4)
        tools = params.get("tools")
        if isinstance(tools, list) and tools:
            names = ", ".join(sanitize_any(as_obj(t).get("name", "?")) for t in tools)
            print(dim(f"  tools offered: {names}  toolChoice: {safe_json(params.get('toolChoice'), indent=None)}"))
        extras = {k: params[k] for k in ("maxTokens", "temperature", "stopSequences", "modelPreferences", "includeContext") if k in params}
        if extras:
            print(dim(f"  {safe_json(extras, indent=None)}"))
        try:
            answer = self.prompter.ask("  reply (or !json {...} / !decline): ")
        except EOFError:
            answer = "!decline"
        stripped = answer.strip()
        if stripped in ("!decline", "!cancel"):
            raise HandlerError(-1, "User rejected sampling request")
        if stripped.startswith("!json"):
            try:
                result = json.loads(stripped[5:])
            except json.JSONDecodeError as e:
                raise HandlerError(-32603, f"invalid JSON typed for sampling result: {e}") from None
            if not isinstance(result, dict):
                raise HandlerError(-32603, "sampling result must be a JSON object")
            return result
        return {
            "role": "assistant",
            "content": {"type": "text", "text": answer},
            "model": "human-via-mcp-test-driver",
            "stopReason": "endTurn",
        }
