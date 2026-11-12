"""Scenario-driven fake MCP server logic, shared by the stdio and HTTP fakes.

A scenario is a JSON-able dict describing how the server behaves.  Handling
a request is a generator of actions, so the transport adapter decides how a
mid-call server request is sent and its answer awaited:

    ("send", message)          write a message to the client
    ("await", None)            wait for the client's next response; the
                               adapter sends it back into the generator
    ("stderr", text)           write to stderr (stdio only)
    ("raw", text)              write raw text as a line (stdio only)
    ("exit", code)             terminate the server process
    ("sleep", seconds)
    ("hold", sub_id)           keep a subscription stream open until the
                               client cancels it (HTTP only; no-op on stdio)

Scenario keys (all optional):
    mode            "legacy" | "modern" | "dual"         (default "legacy")
    versions        revisions supported, oldest first   (default ["2025-11-25"])
    counter_offer   answer initialize with this version regardless
    server_info, capabilities, instructions
    tools, prompts, resources, templates   lists
    page_size       paginate list results
    tool_scripts    {tool: [step, ...]}  steps: {"notify": msg} {"request": msg}
                    {"reply": result} {"reply_error": err} {"stderr": s} {"exit": n}
                    {"raw": s} {"sleep": s} {"mrtr": {key: request}}
                    {"task": {"polls": n, "input": {key: request}?, "result": {...}}}
                      answers with a task handle: 2025-11-25 style ({"task": ...})
                      if the call had params.task, else the 2026 extension style
    resource_contents  {uri: [contents]}
    completions     list of values for completion/complete
    on_unknown_pre_init  "error" | "silent" | "exit"   (legacy servers)
    after_initialized    [notification, ...]  sent once initialized
    batch_responses  answer with single-element batch arrays
    log_path        append every received message as a JSON line
    omit_result_type  modern replies without resultType/serverInfo (to provoke findings)
    skills          entries for skills/list (skills/get finds by uri)
    listen_support  filter keys the server honours (default: all four)
    listen_events   notifications sent on a subscriptions/listen stream
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

Action = tuple[str, Any]
Handler = Generator[Action, Any, None]

META = "io.modelcontextprotocol/"
_REMOVED_IN_MODERN = {"ping", "logging/setLevel", "resources/subscribe", "resources/unsubscribe"}


def result(req_id: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": value}


def error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


class FakeMcp:
    def __init__(self, scenario: dict[str, Any]) -> None:
        self.s = scenario
        self.mode = scenario.get("mode", "legacy")
        self.versions = scenario.get("versions", ["2025-11-25"])
        self.initialized = False
        self.negotiated: str | None = None
        self._next_id = 1000
        self.subscriptions: set[Any] = set()
        self.tasks: dict[str, dict[str, Any]] = {}

    # ── helpers ────────────────────────────────────────────────────────

    def log(self, msg: Any) -> None:
        path = self.s.get("log_path")
        if path:
            with open(path, "a") as f:
                f.write(json.dumps(msg) + "\n")

    def server_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _wrap(self, value: dict[str, Any]) -> dict[str, Any]:
        if self.mode != "legacy" and self.negotiated is None and not self.s.get("omit_result_type"):
            value.setdefault("resultType", "complete")
            value.setdefault("_meta", {})[META + "serverInfo"] = self.server_info()
        return value

    def reply(self, req_id: Any, value: dict[str, Any]) -> Any:
        msg = result(req_id, self._wrap(value))
        return [msg] if self.s.get("batch_responses") else msg

    def server_info(self) -> dict[str, Any]:
        return self.s.get("server_info", {"name": "fake-server", "version": "1.0"})

    def _page(self, items: list[Any], params: dict[str, Any], key: str) -> dict[str, Any]:
        size = self.s.get("page_size")
        if not size:
            return {key: items}
        start = int(params.get("cursor") or 0)
        out: dict[str, Any] = {key: items[start : start + size]}
        if start + size < len(items):
            out["nextCursor"] = str(start + size)
        return out

    # ── dispatch ───────────────────────────────────────────────────────

    def handle(self, msg: Any) -> Handler:
        """Actions for one incoming message (request, notification, or junk)."""
        self.log(msg)
        if isinstance(msg, list):
            for m in msg:
                yield from self.handle(m)
            return
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            yield ("send", error(None, -32600, "Invalid Request"))
            return
        method = msg.get("method")
        if method is None:
            return  # a stray response; the adapter routes awaited ones itself
        params = msg.get("params") or {}
        if "id" not in msg:
            if method == "notifications/cancelled":
                sub = params.get("requestId")
                if sub in self.subscriptions:
                    self.subscriptions.discard(sub)
                    yield ("send", self.listen_closed(sub))
                return
            if method == "notifications/initialized":
                self.initialized = True
                for n in self.s.get("after_initialized", []):
                    yield ("send", n)
            return
        req_id = msg["id"]
        meta = params.get("_meta") or {}
        modern_req = META + "protocolVersion" in meta

        if method == "initialize":
            yield from self._initialize(req_id, params)
            return
        if method == "server/discover":
            yield from self._discover(req_id, meta)
            return
        if self.mode == "legacy" and not self.negotiated:
            yield from self._pre_init(req_id)
            return
        if self.mode == "modern" or (self.mode == "dual" and modern_req and not self.negotiated):
            version = meta.get(META + "protocolVersion")
            if version not in self.versions:
                yield ("send", error(req_id, -32022, "Unsupported protocol version",
                                     {"supported": self.versions, "requested": version}))
                return
        if (modern_req or self.mode == "modern") and method in _REMOVED_IN_MODERN:
            yield ("send", error(req_id, -32601, f"Method not found: {method}"))
            return
        yield from self._method(req_id, method, params)

    def _initialize(self, req_id: Any, params: dict[str, Any]) -> Handler:
        if self.mode == "modern":
            yield ("send", error(req_id, -32601,
                                 f"initialize is not supported; this server speaks {', '.join(self.versions)}"))
            return
        legacy = [v for v in self.versions if v < "2026"]
        offered = params.get("protocolVersion")
        version = self.s.get("counter_offer") or (offered if offered in legacy else legacy[-1])
        self.negotiated = version
        value: dict[str, Any] = {
            "protocolVersion": version,
            "capabilities": self.s.get("capabilities", {"tools": {}}),
            "serverInfo": self.server_info(),
        }
        if "instructions" in self.s:
            value["instructions"] = self.s["instructions"]
        yield ("send", result(req_id, value))

    def _discover(self, req_id: Any, meta: dict[str, Any]) -> Handler:
        if self.mode == "legacy":
            yield from self._pre_init(req_id)
            return
        version = meta.get(META + "protocolVersion")
        modern = [v for v in self.versions if v >= "2026"]
        if version not in modern:
            yield ("send", error(req_id, -32022, "Unsupported protocol version",
                                 {"supported": modern, "requested": version}))
            return
        value: dict[str, Any] = {
            "supportedVersions": modern,
            "capabilities": self.s.get("capabilities", {"tools": {}}),
            "ttlMs": 60000,
            "cacheScope": "public",
        }
        if "instructions" in self.s:
            value["instructions"] = self.s["instructions"]
        yield ("send", result(req_id, self._wrap(value)))

    def _pre_init(self, req_id: Any) -> Handler:
        how = self.s.get("on_unknown_pre_init", "error")
        if how == "silent":
            return
        if how == "exit":
            yield ("exit", 3)
            return
        yield ("send", error(req_id, -32600, "Server not initialized"))

    def _method(self, req_id: Any, method: str, params: dict[str, Any]) -> Handler:
        lists = {
            "tools/list": ("tools", "tools"),
            "prompts/list": ("prompts", "prompts"),
            "resources/list": ("resources", "resources"),
            "resources/templates/list": ("templates", "resourceTemplates"),
        }
        if method in lists:
            key, out_key = lists[method]
            value = self._page(self.s.get(key, []), params, out_key)
            if self.mode != "legacy" and self.negotiated is None:
                value.update({"ttlMs": 1000, "cacheScope": "private"})
            yield ("send", self.reply(req_id, value))
        elif method == "tools/call":
            yield from self._call_tool(req_id, params)
        elif method in ("ping", "logging/setLevel", "resources/subscribe", "resources/unsubscribe"):
            yield ("send", self.reply(req_id, {}))
        elif method == "resources/read":
            uri = params.get("uri")
            contents = self.s.get("resource_contents", {}).get(uri)
            if contents is None:
                code = -32602 if self.mode == "modern" else -32002
                yield ("send", error(req_id, code, "Resource not found", {"uri": uri}))
            else:
                yield ("send", self.reply(req_id, {"contents": contents}))
        elif method == "prompts/get":
            args = params.get("arguments") or {}
            text = f"prompt {params.get('name')} with {json.dumps(args, sort_keys=True)}"
            yield ("send", self.reply(req_id, {
                "messages": [{"role": "user", "content": {"type": "text", "text": text}}]
            }))
        elif method == "subscriptions/listen":
            flt = params.get("notifications") or {}
            support = self.s.get("listen_support", [
                "toolsListChanged", "promptsListChanged", "resourcesListChanged", "resourceSubscriptions"])
            acked = {k: v for k, v in flt.items() if k in support}
            self.subscriptions.add(req_id)
            tag = {META + "subscriptionId": req_id}
            yield ("send", {"jsonrpc": "2.0", "method": "notifications/subscriptions/acknowledged",
                            "params": {"_meta": tag, "notifications": acked}})
            for ev in self.s.get("listen_events", []):
                ev = json.loads(json.dumps(ev))
                ev.setdefault("params", {}).setdefault("_meta", {}).update(tag)
                yield ("send", ev)
            yield ("hold", req_id)
        elif method.startswith("tasks/") and self._has_tasks():
            yield ("send", self._task_method(req_id, method, params))
        elif method == "skills/list":
            yield ("send", self.reply(req_id, {"skills": self.s.get("skills", [])}))
        elif method == "skills/get":
            entry = next((e for e in self.s.get("skills", []) if e.get("uri") == params.get("uri")), None)
            if entry is None:
                yield ("send", error(req_id, -32602, "Unknown skill"))
            else:
                yield ("send", self.reply(req_id, entry))
        elif method == "completion/complete":
            values = self.s.get("completions", [])
            prefix = (params.get("argument") or {}).get("value", "")
            yield ("send", self.reply(req_id, {
                "completion": {"values": [v for v in values if v.startswith(prefix)], "hasMore": False}
            }))
        else:
            yield ("send", error(req_id, -32601, f"Method not found: {method}"))

    # ── tasks (2025-11-25 core and the 2026 extension) ─────────────────

    def _has_tasks(self) -> bool:
        """Only scenarios that use tasks implement the tasks/* methods."""
        scripts = self.s.get("tool_scripts", {}).values()
        return "tasks" in self.s.get("capabilities", {}) or any("task" in step for s in scripts for step in s)

    def _new_task(self, spec: dict[str, Any], *, core: bool, args: dict[str, Any]) -> dict[str, Any]:
        tid = f"task-{len(self.tasks) + 1}"
        self.tasks[tid] = {"spec": spec, "core": core, "polls": 0, "status": "working",
                           "input_done": "input" not in spec, "responses": None, "args": args}
        view = self._task_view(tid)
        return {"task": view} if core else {**view, "resultType": "task"}

    def _task_view(self, tid: str) -> dict[str, Any]:
        t = self.tasks[tid]
        view: dict[str, Any] = {"taskId": tid, "status": t["status"], "createdAt": "2026-01-01T00:00:00Z",
                                "lastUpdatedAt": "2026-01-01T00:00:00Z"}
        if t["core"]:
            view.update({"ttl": None, "pollInterval": 10})
        else:
            view.update({"ttlMs": None, "pollIntervalMs": 10})
            if t["status"] == "input_required":
                view["inputRequests"] = t["spec"]["input"]
            if t["status"] == "completed":
                view["result"] = self._task_result(tid)
        return view

    def _task_result(self, tid: str) -> dict[str, Any]:
        t = self.tasks[tid]
        result = dict(t["spec"].get("result", {"content": [{"type": "text", "text": "done"}]}))
        if t["responses"] is not None:
            result["content"] = [{"type": "text", "text": json.dumps(t["responses"])}]
        return result

    def _advance(self, tid: str) -> None:
        t = self.tasks[tid]
        if t["status"] in ("completed", "cancelled", "failed"):
            return
        t["polls"] += 1
        if not t["input_done"] and not t["core"]:
            t["status"] = "input_required"
        elif t["polls"] >= t["spec"].get("polls", 1):
            t["status"] = "completed"
        else:
            t["status"] = "working"

    def _task_method(self, req_id: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "tasks/list":
            return self.reply(req_id, {"tasks": [self._task_view(tid) for tid in self.tasks]})
        tid = params.get("taskId")
        if tid not in self.tasks:
            return error(req_id, -32602, f"Unknown task {tid}")
        t = self.tasks[tid]
        if method == "tasks/get":
            self._advance(tid)
            return self.reply(req_id, self._task_view(tid))
        if method == "tasks/update":
            t["responses"] = params.get("inputResponses")
            t["input_done"] = True
            t["status"] = "working"
            return self.reply(req_id, {})
        if method == "tasks/cancel":
            if t["status"] in ("completed", "cancelled", "failed"):
                return error(req_id, -32602, "Task already finished")
            t["status"] = "cancelled"
            return self.reply(req_id, self._task_view(tid))
        if method == "tasks/result":
            t["status"] = "completed"
            return self.reply(req_id, self._task_result(tid))
        return error(req_id, -32601, f"Method not found: {method}")

    def listen_closed(self, sub: Any) -> dict[str, Any]:
        return result(sub, self._wrap({"_meta": {META + "subscriptionId": sub}}))

    def _call_tool(self, req_id: Any, params: dict[str, Any]) -> Handler:
        name = params.get("name")
        args = params.get("arguments") or {}
        script = self.s.get("tool_scripts", {}).get(name)
        if script is None:
            if any(t.get("name") == name for t in self.s.get("tools", [])):
                yield ("send", self.reply(req_id, {
                    "content": [{"type": "text", "text": json.dumps(args, sort_keys=True)}]
                }))
            else:
                yield ("send", error(req_id, -32602, f"Unknown tool: {name}"))
            return
        answers: list[Any] = []
        for step in script:
            if "notify" in step:
                yield ("send", step["notify"])
            elif "request" in step:
                req = dict(step["request"])
                req.setdefault("jsonrpc", "2.0")
                req["id"] = self.server_id()
                yield ("send", req)
                answers.append((yield ("await", None)))
            elif "mrtr" in step:
                responses = params.get("inputResponses")
                if responses is None:
                    yield ("send", self.reply(req_id, {
                        "resultType": "input_required",
                        "inputRequests": step["mrtr"],
                        "requestState": "state-1",
                    }))
                    return
                answers.append({"inputResponses": responses, "requestState": params.get("requestState")})
            elif "task" in step:
                yield ("send", self.reply(req_id, self._new_task(step["task"], core="task" in params, args=args)))
                return
            elif "reply" in step:
                value = dict(step["reply"])
                if answers and "content" not in value:
                    value["content"] = [{"type": "text", "text": json.dumps(answers)}]
                yield ("send", self.reply(req_id, value))
                return
            elif "reply_error" in step:
                e = step["reply_error"]
                yield ("send", error(req_id, e["code"], e["message"], e.get("data")))
                return
            elif "stderr" in step:
                yield ("stderr", step["stderr"])
            elif "raw" in step:
                yield ("raw", step["raw"])
            elif "sleep" in step:
                yield ("sleep", step["sleep"])
            elif "exit" in step:
                yield ("exit", step["exit"])
                return
        yield ("send", self.reply(req_id, {"content": [{"type": "text", "text": json.dumps(answers)}]}))
