# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tasks: long-running requests the client polls for.

Two generations, with different shapes:

  - 2025-11-25 core tasks (experimental, SEP-1686): the client asks with
    params.task on tools/call; the result is {"task": Task}; the client
    polls tasks/get and fetches the outcome with the blocking tasks/result.
    tasks/list and tasks/cancel exist; status arrives as
    notifications/tasks/status.
  - 2026-07-28 tasks extension (io.modelcontextprotocol/tasks, SEP-2663):
    the server decides; a result with resultType "task" is the task itself.
    tasks/get returns it with result/error/inputRequests inlined; input goes
    back with tasks/update; tasks/cancel ends it.  No list, no result.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .color import bold, cyan, dim, eprint, red, yellow
from .handlers import HandlerError
from .messages import JsonObject, JsonValue, as_obj
from .sanitize import sanitize, sanitize_any

if TYPE_CHECKING:
    from .protocol import McpSession

TASKS_EXTENSION = "io.modelcontextprotocol/tasks"
TERMINAL = {"completed", "failed", "cancelled"}
_MIN_POLL = 0.2
_MAX_POLL = 5.0
_DEFAULT_POLL = 1.0

Generation = Literal["core", "extension"]


class _Detach(Exception):
    """Stop following a task, leaving it running."""


@dataclass
class TaskHandle:
    task_id: str
    generation: Generation
    task: JsonObject


def task_in(result: JsonValue) -> TaskHandle | None:
    """The task handle a result carries, if it is one."""
    r = as_obj(result)
    if r.get("resultType") == "task" and isinstance(r.get("taskId"), str):
        return TaskHandle(r["taskId"], "extension", r)
    inner = as_obj(r.get("task"))
    if isinstance(inner.get("taskId"), str) and "content" not in r:
        return TaskHandle(inner["taskId"], "core", inner)
    return None


def describe(task: JsonObject) -> str:
    status = sanitize_any(task.get("status", "?"))
    line = f"task {sanitize_any(task.get('taskId', '?'))}: {status}"
    if task.get("statusMessage"):
        line += f" — {sanitize_any(task['statusMessage'])}"
    return line


def _interval(task: JsonObject) -> float:
    raw = task.get("pollIntervalMs", task.get("pollInterval"))
    seconds = raw / 1000 if isinstance(raw, (int, float)) else _DEFAULT_POLL
    return min(max(seconds, _MIN_POLL), _MAX_POLL)


def follow(session: McpSession, handle: TaskHandle) -> JsonObject:
    """Poll a task to its end and return a response-shaped dict for display.

    Ctrl-C stops following (the task carries on); /tasks get, /tasks wait or
    /tasks cancel can pick it up again.
    """
    eprint(cyan(f"  {describe(handle.task)}; following (Ctrl-C to detach)"))
    answered: set[str] = set()
    task = handle.task
    last_status = task.get("status")
    try:
        while task.get("status") not in TERMINAL:
            if task.get("status") == "input_required":
                if handle.generation == "core":
                    # 2025-11-25: tasks/result delivers the server's requests on its stream.
                    return session.send_request("tasks/result", {"taskId": handle.task_id})
                _update_with_input(session, handle.task_id, as_obj(task.get("inputRequests")), answered)
            time.sleep(_interval(task))
            task = session.call("tasks/get", {"taskId": handle.task_id})
            if task.get("status") != last_status:
                eprint(cyan(f"  {describe(task)}"))
                last_status = task.get("status")
    except (KeyboardInterrupt, _Detach):
        eprint(yellow(f"  stopped following task {sanitize(handle.task_id)}; it is still running"))
        return {"result": {"task": task}}
    if handle.generation == "core":
        return session.send_request("tasks/result", {"taskId": handle.task_id})
    if task.get("status") == "completed":
        return {"result": as_obj(task.get("result"))}
    if task.get("status") == "failed":
        return {"error": as_obj(task.get("error")) or {"code": None, "message": "task failed"}}
    return {"result": {"task": task}}


def _update_with_input(session: McpSession, task_id: str, requests: JsonObject, answered: set[str]) -> None:
    responses: JsonObject = {}
    for key, req in requests.items():
        if key in answered:
            continue  # the spec asks clients to deduplicate keys across polls
        inner = as_obj(req)
        method = str(inner.get("method"))
        eprint(cyan(f"  <<< task input requested: {sanitize(method)} (key {sanitize(str(key))})"))
        if not session.handler_registry.has(method):
            eprint(red(f"  This client cannot answer {sanitize(method)}; enable it and use /tasks wait."))
            raise _Detach
        try:
            responses[key] = session.handler_registry.dispatch(method, as_obj(inner.get("params")))
        except HandlerError as e:
            eprint(yellow(f"  {sanitize(method)} declined: {sanitize(e.message)}"))
            raise _Detach from None
        answered.add(key)
    if responses:
        session.call("tasks/update", {"taskId": task_id, "inputResponses": responses})


def print_task(task: JsonObject) -> None:
    print(bold(describe(task)))
    for key in ("createdAt", "lastUpdatedAt", "ttl", "ttlMs", "pollInterval", "pollIntervalMs"):
        if key in task:
            print(dim(f"  {key}: {sanitize_any(task[key])}"))
