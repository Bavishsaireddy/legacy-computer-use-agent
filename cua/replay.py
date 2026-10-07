"""Deterministic replay: the production path. No model is consulted here.

Every wait is a race between known states, checked in this order:
  app failure  -> hard failure
  outcome      -> legitimate business result, stop
  recovery     -> bounded response, then resume or restart
  next target  -> the previous step worked; go on
Anything else, by the deadline, is an unknown state and a hard failure.
"""

from __future__ import annotations

import time
from typing import Callable

from .evidence import RunLog
from .handoff import Decision, Intervention, Operator, escalate
from .policy import Approval, ApprovalRequired, PolicyViolation
from .runtime import ConfigError, Session, open_session, start_log
from .schema import (AppProfile, Capability, ErrorDetail, Outcome, OutcomeResult, Recovery,
                     RunResult, Step, TenantBinding, check_param, parse_value, render)
from .surface import ActionError, ControlError, TargetError

SLOW_MS = 2000
ESCALATABLE = {"TARGET_NOT_FOUND", "TARGET_AMBIGUOUS", "CHECKPOINT_TIMEOUT", "ACTION_FAILED",
               "APP_ERROR", "RECOVERY_EXHAUSTED", "UNSAFE_RESTART"}
TRANSIENT = {"CHECKPOINT_TIMEOUT", "ACTION_FAILED", "APP_ERROR", "RECOVERY_EXHAUSTED"}


class Failure(Exception):
    def __init__(self, kind: str, phase: str, expected: str = "", observed: str = "") -> None:
        super().__init__(f"{kind}: expected {expected}; observed {observed}")
        self.kind, self.phase, self.expected, self.observed = kind, phase, expected, observed


class Replayer:
    def __init__(self, cap: Capability, session: Session, operator: Operator | None = None,
                 on_unknown: Callable[[int, Session], Outcome | None] | None = None,
                 max_handoffs: int = 2, dry_run: bool = False) -> None:
        self.cap, self.s, self.operator = cap, session, operator
        self.dry_run = dry_run
        self.on_unknown = on_unknown
        self.max_handoffs = max_handoffs
        self.outputs: dict = {}
        self.side_effects = "none"
        self.recovered: dict[str, int] = {}
        self.handoffs = 0
        self.notes: list[str] = []
        # Controls that hold sensitive values, masked in failure screenshots.
        self.mask = [st.target for st in cap.steps if st.value or st.output]

    # -- waiting -------------------------------------------------------------
    def settle(self, i: int) -> Outcome | str | None:
        """Wait until step i can run (or, past the last step, until success).
        Returns an Outcome, "restart", or None when the expected state is reached."""
        steps = self.cap.steps
        prev = steps[i - 1] if i else None
        expect = steps[i].target if i < len(steps) else self.cap.success
        outcomes = [*(prev.outcomes if prev else []), *self.s.profile.outcomes]
        timeout = (prev.timeout_ms if prev else 8000) / 1000
        started = time.monotonic()
        deadline = started + timeout
        while True:
            self.s.surface.settle()
            for failure in self.s.profile.failures:
                if self.s.present(failure.when):
                    raise Failure("APP_ERROR", "wait", self.describe(expect), f"{failure.name}: {self.s.observed()}")
            for outcome in outcomes:
                if self.s.present(outcome.when):
                    return outcome
            recovery = next((r for r in [*self.s.profile.recoveries, *self.cap.recoveries]
                             if self.s.present(r.when)), None)
            if recovery:
                if self.recover(recovery) == "restart":
                    return "restart"
                deadline = time.monotonic() + timeout
                continue
            if self.s.present(expect):
                waited = int((time.monotonic() - started) * 1000)
                if waited > SLOW_MS:
                    self.s.log.event("slow_wait", before_step=steps[i].id if i < len(steps) else "success", ms=waited)
                return None
            if time.monotonic() > deadline:
                if self.on_unknown and (found := self.on_unknown(i, self.s)):
                    return found
                raise Failure("CHECKPOINT_TIMEOUT", "wait", self.describe(expect), self.s.observed())
            self.s.surface.wait(100)

    def describe(self, target: str) -> str:
        return f"{target} ({self.s.target(target).description})"

    def recover(self, r: Recovery) -> str:
        self.recovered[r.name] = self.recovered.get(r.name, 0) + 1
        if self.recovered[r.name] > r.max:
            raise Failure("RECOVERY_EXHAUSTED", "wait", f"{r.name} at most {r.max}x", self.s.observed())
        if r.then == "restart" and self.side_effects != "none":
            # Re-running the flow would repeat an irreversible step.
            raise Failure("UNSAFE_RESTART", "wait", f"{r.name} requires restarting the flow",
                          f"side effects are {self.side_effects}")
        try:
            for a in r.do:
                self.s.surface.act(a.action, self.s.target(a.target), self.s.values, render(a.value, self.s.values))
            if r.then == "restart":
                self.s.surface.open(self.cap.entry)
        except (TargetError, ActionError, PolicyViolation, ApprovalRequired, ControlError) as e:
            raise Failure("ACTION_FAILED", "wait", f"recovery {r.name}", str(e)) from e
        self.s.log.event("recovery_applied", name=r.name, then=r.then, count=self.recovered[r.name])
        return r.then

    # -- acting --------------------------------------------------------------
    def do(self, step: Step) -> None:
        target, before = self.s.target(step.target), self.side_effects
        try:
            if step.action == "extract":
                raw = self.s.surface.read(target, self.s.values)
                spec = self.cap.outputs[step.output]
                self.outputs[step.output] = parse_value(spec.type, raw)
                if spec.sensitivity != "public":
                    self.s.redactor.add(raw, f"[output:{step.output}]")
                    self.s.redactor.add(str(self.outputs[step.output]), f"[output:{step.output}]")
            else:
                approval = None
                if step.risk == "irreversible":
                    approval = Approval("caller-confirm", f"{self.cap.capability}@{self.cap.version} approved")
                    self.side_effects = "unknown"  # until the next checkpoint proves it landed
                risk = self.s.surface.act(step.action, target, self.s.values, render(step.value, self.s.values), approval)
                if risk == "irreversible":
                    self.side_effects = "unknown"
        except ActionError as e:
            raise Failure("ACTION_FAILED", "act", self.describe(step.target), str(e)) from e
        except (TargetError, PolicyViolation, ApprovalRequired, ControlError, ValueError) as e:
            self.side_effects = before  # refused before anything was sent to the app
            kind = {TargetError: getattr(e, "kind", ""), PolicyViolation: "POLICY_BLOCKED",
                    ApprovalRequired: "APPROVAL_REQUIRED", ControlError: "CONTROL_VIOLATION",
                    ValueError: "OUTPUT_INVALID"}[type(e)]
            raise Failure(kind, "act", self.describe(step.target), str(e)) from e
        self.s.log.event("step_done", step=step.id, action=step.action, target=step.target, risk=step.risk)

    # -- escalation ----------------------------------------------------------
    def ask_human(self, f: Failure, step: Step | None, evidence: dict) -> Decision:
        if f.kind not in ESCALATABLE or self.handoffs >= self.max_handoffs:
            return Decision("unrouted", by="nobody")
        # With an unverified irreversible step, a blind retry could post twice.
        options = ["done", "abort"] if self.side_effects == "unknown" else ["retry", "done", "abort"]
        request = Intervention(
            id=f"{self.s.log.run_id}-h{self.handoffs + 1}", run_id=self.s.log.run_id, kind="stuck",
            capability=f"{self.cap.capability}@{self.cap.version}", step=step.id if step else "success",
            reason=f"{f.kind} while {'acting' if f.phase == 'act' else 'waiting'}: {f.observed}"[:500],
            expected=f.expected, options=options, **evidence)
        decision = escalate(request, self.operator, self.s.surface, self.s.log)
        if self.operator:
            self.handoffs += 1
        return decision

    # -- main loop -------------------------------------------------------------
    def run(self) -> RunResult:
        steps, i = self.cap.steps, 0
        while True:
            step = steps[i] if i < len(steps) else None
            try:
                signal = self.settle(i)
                if i and steps[i - 1].risk == "irreversible" and self.side_effects == "unknown" and signal is None:
                    self.side_effects = "committed"
                if signal == "restart":
                    i, self.outputs = 0, {}
                    continue
                if isinstance(signal, Outcome):
                    message = self.s.surface.read(self.s.target(signal.when), self.s.values)
                    return self.result("business_outcome", outcome=OutcomeResult(
                        name=signal.name, description=signal.description, message=message))
                if step is None:
                    return self.result("success")
                if self.dry_run and step.risk == "irreversible":
                    self.notes.append(f"dry run: stopped before irreversible step {step.id}")
                    return self.result("success")
                self.do(step)
                i += 1
            except Failure as f:
                self.s.log.event("failure", kind=f.kind, phase=f.phase, step=step.id if step else "success",
                                 expected=f.expected, observed=f.observed)
                evidence = self.s.capture(f"failure-{len(self.notes) + 1}", self.mask)
                decision = self.ask_human(f, step, evidence)
                self.notes.append(f"{f.kind} at {step.id if step else 'success'}: operator -> {decision.action}")
                if decision.action == "retry":
                    continue
                if decision.action == "done":
                    # The human says the state is fixed. Trust nothing: skip the step only
                    # if it was the action that failed, then re-verify by settling again.
                    i += f.phase == "act"
                    continue
                stuck = self.side_effects == "unknown" or decision.action == "timeout"
                return self.result("needs_human" if stuck else "failed", error=ErrorDetail(
                    kind=f.kind, step=step.id if step else None, phase=f.phase, expected=f.expected,
                    observed=f.observed, evidence=evidence,
                    retryable=f.kind in TRANSIENT and self.side_effects == "none"))

    def result(self, status: str, **extra) -> RunResult:
        return RunResult(
            status=status, capability=self.cap.capability, version=self.cap.version, run_id=self.s.log.run_id,
            outputs=self.outputs if status == "success" else {}, side_effects=self.side_effects,
            recoveries=[f"{k} x{v}" for k, v in self.recovered.items()],
            warnings=[*self.s.surface.warnings, *self.notes], handoffs=self.handoffs, **extra)


def preflight(cap: Capability, params: dict[str, str], confirm: bool, dry_run: bool = False) -> ErrorDetail | None:
    """Everything that can be refused before touching the application."""
    problems = [f"{k}: missing" for k in cap.params if k not in params]
    problems += [f"{k}: not a declared parameter" for k in params if k not in cap.params]
    problems += [p for k, spec in cap.params.items() if k in params and (p := check_param(k, spec, params[k]))]
    if problems:
        return ErrorDetail(kind="PARAM_INVALID", phase="preflight", expected="valid parameters", observed="; ".join(problems))
    risky = [s.id for s in cap.steps if s.risk == "irreversible"]
    if risky and not dry_run and (cap.status != "approved" or not confirm):
        return ErrorDetail(
            kind="APPROVAL_REQUIRED", phase="preflight", step=risky[0],
            expected="status: approved and an explicit confirm from the caller",
            observed=f"status: {cap.status}, confirm: {confirm}")
    return None


def finish(log: RunLog, result: RunResult) -> RunResult:
    log.event("run_finished", status=result.status, side_effects=result.side_effects,
              error=result.error.kind if result.error else None)
    log.write_json("result.json", result.model_dump(mode="json"))  # redacted copy; the caller gets the real one
    return result


def replay(cap: Capability, profile: AppProfile, tenant: TenantBinding, params: dict[str, str], *,
           confirm: bool = False, operator: Operator | None = None, headed: bool = False,
           on_unknown=None, dry_run: bool = False, runs_dir: str = "runs") -> RunResult:
    log = start_log("replay", cap.capability, params, runs_dir)
    log.event("replay_started", capability=cap.capability, version=cap.version, status=cap.status,
              tenant=tenant.tenant, params=params, dry_run=dry_run)
    if error := preflight(cap, params, confirm, dry_run):
        return finish(log, RunResult(status="failed", capability=cap.capability, version=cap.version,
                                     run_id=log.run_id, error=error))
    if cap.app.product != profile.product:
        raise ConfigError(f"{cap.capability} targets {cap.app.product}, not {profile.product}")
    session = open_session(profile, tenant, log, params, cap.targets, headed)
    try:
        session.surface.open(cap.entry)
        result = Replayer(cap, session, operator, on_unknown, dry_run=dry_run).run()
        log.event("control_history", transfers=session.surface.lease.transfers)
        return finish(log, result)
    finally:
        session.surface.close()
