"""Human-in-the-loop: who controls the live session, and how control moves.

The lease is the single source of truth for "who is in control". The surface
refuses automation actions unless the lease says "automation"; human input is
recorded only while it says "human". The operator console is a seam: the
ConsoleOperator below is a deliberately minimal stand-in for a real one.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Protocol

from pydantic import BaseModel

from .evidence import RunLog, now

Owner = Literal["automation", "human"]


@dataclass
class Lease:
    owner: Owner = "automation"
    transfers: list[dict] = field(default_factory=list)

    def give(self, to: Owner, reason: str) -> None:
        self.transfers.append({"ts": now(), "from": self.owner, "to": to, "reason": reason})
        self.owner = to


class Intervention(BaseModel):
    id: str
    run_id: str
    kind: Literal["stuck", "approval"]
    capability: str
    goal: str = ""
    step: str | None = None
    reason: str
    expected: str = ""
    screenshot: str = ""
    snapshot: str = ""
    options: list[str]


@dataclass
class Decision:
    action: str  # one of Intervention.options, or "timeout"
    by: str = "operator"
    note: str = ""


class Operator(Protocol):
    def handle(self, request: Intervention, surface) -> Decision: ...


class ConsoleOperator:
    """Mock operator console: the request is printed to the terminal, the human
    works in the same (headed) browser window, then types a decision."""

    def __init__(self, timeout_s: float = 300, stream=None) -> None:
        self.timeout_s = timeout_s
        self.stream = stream or sys.stdin

    def handle(self, request: Intervention, surface) -> Decision:
        print(f"\n=== INTERVENTION {request.id} ({request.kind}) ===", file=sys.stderr)
        print(f"capability: {request.capability}   step: {request.step}", file=sys.stderr)
        print(f"reason:     {request.reason}", file=sys.stderr)
        if request.expected:
            print(f"expected:   {request.expected}", file=sys.stderr)
        print(f"screenshot: {request.screenshot}", file=sys.stderr)
        print("You have control of the browser window. When finished, type one of: "
              + " / ".join(request.options), file=sys.stderr)
        answer: list[str] = []
        reader = threading.Thread(target=lambda: answer.append(self.stream.readline().strip()), daemon=True)
        reader.start()
        deadline = time.monotonic() + self.timeout_s
        while reader.is_alive() and time.monotonic() < deadline:
            surface.wait(200)  # keeps the browser event channel pumping so human input is captured
        choice = answer[0] if answer else ""
        if choice not in request.options:
            return Decision("timeout" if not answer else "abort", note=f"unrecognised answer {choice!r}" if answer else "")
        return Decision(choice)


class FileOperator:
    """The seam a real operator console plugs into: requests are published to a queue
    directory and the decision is read back from it. `python -m cua console` is one such console."""

    def __init__(self, queue: str | Path = "runs/_pending", timeout_s: float = 300) -> None:
        self.queue, self.timeout_s = Path(queue), timeout_s

    def handle(self, request: Intervention, surface) -> Decision:
        self.queue.mkdir(parents=True, exist_ok=True)
        pending = self.queue / f"{request.id}.json"
        answer = self.queue / f"{request.id}.decision.json"
        pending.write_text(request.model_dump_json(indent=2))
        deadline = time.monotonic() + self.timeout_s
        try:
            while time.monotonic() < deadline:
                if answer.exists():
                    data = json.loads(answer.read_text())
                    if data.get("action") in request.options:
                        return Decision(data["action"], by=data.get("by", "console-operator"), note=data.get("note", ""))
                    answer.unlink()
                surface.wait(200)  # keeps the browser event channel pumping so human input is captured
            return Decision("timeout")
        finally:
            pending.unlink(missing_ok=True)
            answer.unlink(missing_ok=True)


class ScriptedOperator:
    """An operator played by a function, for tests and unattended demos. The
    function receives the surface's raw page and acts as the human would."""

    def __init__(self, decision: str, act: Callable | None = None) -> None:
        self.decision, self.act = decision, act
        self.requests: list[Intervention] = []

    def handle(self, request: Intervention, surface) -> Decision:
        self.requests.append(request)
        if self.act:
            self.act(surface.page)
            surface.wait(200)
        return Decision(self.decision, by="scripted-operator")


def escalate(request: Intervention, operator: Operator | None, surface, log: RunLog) -> Decision:
    """Route an intervention, transfer control, and take it back.

    Returns Decision("unrouted") when no operator is attached.
    """
    log.write_json(f"intervention-{request.id}.json", request.model_dump())
    log.event("intervention_requested", **request.model_dump())
    if operator is None:
        log.event("intervention_unrouted", id=request.id)
        return Decision("unrouted", by="nobody")
    surface.lease.give("human", request.reason)
    log.event("control_transferred", to="human", id=request.id)
    try:
        decision = operator.handle(request, surface)
    finally:
        surface.settle()
        actions = surface.drain_human_events()
        surface.lease.give("automation", "operator finished")
    log.event("human_actions", id=request.id, actions=actions)
    log.event("control_transferred", to="automation", id=request.id,
              decision=decision.action, by=decision.by, note=decision.note)
    return decision
