"""Turn a discovery trace into a capability artifact. The model transcript is not an input."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .evidence import now
from .schema import AppRef, Capability, Output, Param, Provenance, Step, Target


@dataclass
class TraceStep:
    action: str
    target: Target
    value: str | None = None  # already a template
    output: str | None = None
    output_type: str = "string"
    risk: str = "safe"
    note: str = ""


def to_template(text: str | None, params: dict[str, str]) -> str | None:
    """Replace literal parameter values with {{name}} so nothing recorded is
    specific to the example the flow was discovered with."""
    if text is None:
        return None
    for name, value in sorted(params.items(), key=lambda kv: -len(kv[1])):
        if len(value) >= 3:
            text = text.replace(value, "{{%s}}" % name)
    return text


def template_target(target: Target, params: dict[str, str]) -> Target:
    def walk(obj):
        if isinstance(obj, str):
            return to_template(obj, params)
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(v) for v in obj]
        return obj

    return Target.model_validate(walk(target.model_dump()))


def slug(target: Target) -> str:
    words = re.sub(r"\{\{.*?\}\}|\(.*?\)", " ", target.description.lower())
    words = re.sub(r"\bin row\b", " ", words)
    return re.sub(r"[^a-z0-9]+", "_", words).strip("_")[:40] or "target"


class TargetTable:
    """Names targets so steps, outcomes and tenant overrides can refer to them."""

    def __init__(self, targets: dict[str, Target] | None = None) -> None:
        self.targets = targets if targets is not None else {}

    def name(self, target: Target) -> str:
        for name, existing in self.targets.items():
            if existing.locators == target.locators:
                return name
        base, name, n = slug(target), slug(target), 1
        while name in self.targets:
            n += 1
            name = f"{base}_{n}"
        self.targets[name] = target
        return name


def infer_param(value: str) -> Param:
    pattern = rf"\d{{{len(value)}}}" if value.isdigit() else None
    return Param(type="string", pattern=pattern, sensitivity="pii")


def compile_capability(*, name: str, goal: str, product: str, entry: str, params: dict[str, str],
                       trace: list[TraceStep], success: Target, model: str, run_id: str,
                       notes: list[str]) -> Capability:
    table = TargetTable()
    steps: list[Step] = []
    outputs: dict[str, Output] = {}
    for t in trace:
        target = table.name(t.target)
        if steps and t.action == "type" and steps[-1].action == "type" and steps[-1].target == target:
            steps.pop()  # the model retyped the same field; only the final text matters
        if t.output:
            outputs[t.output] = Output(type=t.output_type,
                                       sensitivity="financial" if t.output_type == "money" else "pii")
        steps.append(Step(id="", action=t.action, target=target, value=t.value, output=t.output,
                          risk=t.risk, note=t.note))
    for i, step in enumerate(steps, 1):
        step.id = f"s{i}"
    weak = [n for n, t in table.targets.items() if "weak" in t.description]
    if weak:
        notes = [*notes, f"positional targets need review: {', '.join(weak)}"]
    return Capability(
        capability=name, description=goal, app=AppRef(product=product), entry=entry,
        params={k: infer_param(v) for k, v in params.items()}, outputs=outputs,
        targets=table.targets, steps=steps, success=table.name(success),
        provenance=Provenance(discovered_by=model, discovered_at=now(), discovery_run=run_id,
                              notes=[*notes, "param patterns and sensitivities are inferred; review before approval"]),
    )
