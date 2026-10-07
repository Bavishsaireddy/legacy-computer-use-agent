"""Typed contracts: the capability artifact, its app/tenant layers, and the run result.

Three layers, most general first:
  AppProfile     per vendor product: login, global recoveries/outcomes, policy
  Capability     per task on that product: the recorded flow (the artifact)
  TenantBinding  per institution: base URL, credentials source, target overrides
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1"

Sensitivity = Literal["public", "pii", "financial", "secret"]
ActionType = Literal["click", "type", "select", "extract"]
Risk = Literal["safe", "irreversible"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- targets ---------------------------------------------------------------

class A11yLocator(Model):
    """Accessibility-tree locator: role + accessible name, optionally anchored
    to the table row whose label cell reads `row`. Strings may hold {{param}}."""

    kind: Literal["a11y"] = "a11y"
    frame: list[str] = []  # frame names from the top; never frame indexes
    role: str
    name: str | None = None
    row: str | None = None
    nth: int | None = None  # position among same-role nodes in scope; last resort


class NearTextLocator(Model):
    """Fallback for when the accessibility structure differs: the first control
    of this role that carries, or follows, this visible text."""

    kind: Literal["near_text"] = "near_text"
    frame: list[str] = []
    role: str
    text: str


Locator = Annotated[Union[A11yLocator, NearTextLocator], Field(discriminator="kind")]


class Target(Model):
    description: str = ""
    locators: list[Locator]  # tried in order; each must match exactly one node


# -- flow ------------------------------------------------------------------

class Param(Model):
    type: Literal["string", "integer", "money"] = "string"
    pattern: str | None = None
    sensitivity: Sensitivity = "pii"
    description: str = ""


class Output(Model):
    type: Literal["string", "integer", "money"] = "string"
    sensitivity: Sensitivity = "pii"
    description: str = ""


class Outcome(Model):
    """A legitimate business result (not an error) recognised by a visible target."""

    name: str
    description: str = ""
    when: str  # target name


class Step(Model):
    id: str
    action: ActionType
    target: str
    value: str | None = None  # type/select: template such as "{{member_id}}"
    output: str | None = None  # extract: name of the declared output
    risk: Risk = "safe"
    note: str = ""  # why the step exists, for reviewers
    outcomes: list[Outcome] = []  # checked only while waiting after this step
    timeout_ms: int = 8000


class RecoveryAction(Model):
    action: Literal["click", "type", "select"]
    target: str
    value: str | None = None


class Recovery(Model):
    """A known, recoverable interruption and its bounded response."""

    name: str
    when: str  # target name
    do: list[RecoveryAction]
    then: Literal["resume", "restart"] = "resume"
    max: int = 1


class AppFailure(Model):
    name: str
    when: str


class RiskRule(Model):
    role: str | None = None
    name: str  # regex over the control's accessible name / label


class Policy(Model):
    allowed_paths: list[str]  # fnmatch globs over URL paths on the tenant origin
    allowed_actions: list[ActionType] = ["click", "type", "select", "extract"]
    irreversible: list[RiskRule] = []


class AppRef(Model):
    product: str
    versions: str = "*"


class Provenance(Model):
    discovered_by: str = ""
    discovered_at: str = ""
    discovery_run: str = ""
    verified: bool = False
    verification: list[str] = []
    notes: list[str] = []


class Capability(Model):
    schema_version: str = SCHEMA_VERSION
    capability: str
    version: str = "0.1.0"
    status: Literal["draft", "approved"] = "draft"
    description: str = ""
    app: AppRef
    entry: str = "/"
    params: dict[str, Param] = {}
    outputs: dict[str, Output] = {}
    targets: dict[str, Target]
    steps: list[Step]
    success: str  # target name that must be present at the end
    recoveries: list[Recovery] = []
    provenance: Provenance = Provenance()

    @model_validator(mode="after")
    def references_resolve(self) -> "Capability":
        used = {s.target for s in self.steps} | {o.when for s in self.steps for o in s.outcomes} | {self.success}
        if missing := used - set(self.targets):
            raise ValueError(f"undefined targets: {sorted(missing)}")
        declared = {s.output for s in self.steps if s.action == "extract"}
        if declared != set(self.outputs):
            raise ValueError(f"outputs {sorted(self.outputs)} do not match extract steps {sorted(declared)}")
        return self


class AppProfile(Model):
    product: str
    entry: str = "/"
    targets: dict[str, Target] = {}
    recoveries: list[Recovery] = []
    outcomes: list[Outcome] = []
    failures: list[AppFailure] = []
    policy: Policy


class TenantBinding(Model):
    tenant: str
    product: str
    base_url: str
    secrets_env: dict[str, str] = {}  # secret name -> environment variable
    target_overrides: dict[str, Target] = {}


# -- result ----------------------------------------------------------------

ErrorKind = Literal[
    "PARAM_INVALID", "APPROVAL_REQUIRED", "TARGET_NOT_FOUND", "TARGET_AMBIGUOUS",
    "CHECKPOINT_TIMEOUT", "ACTION_FAILED", "APP_ERROR", "POLICY_BLOCKED",
    "RECOVERY_EXHAUSTED", "UNSAFE_RESTART", "OUTPUT_INVALID", "CONTROL_VIOLATION",
]


class ErrorDetail(Model):
    kind: ErrorKind
    step: str | None = None
    phase: Literal["preflight", "wait", "act"] = "wait"
    expected: str = ""
    observed: str = ""
    retryable: bool = False
    evidence: dict[str, str] = {}


class OutcomeResult(Model):
    name: str
    description: str = ""
    message: str = ""


class RunResult(Model):
    status: Literal["success", "business_outcome", "failed", "needs_human"]
    capability: str
    version: str
    run_id: str
    outputs: dict[str, Any] = {}
    outcome: OutcomeResult | None = None
    error: ErrorDetail | None = None
    # none: no irreversible step attempted. committed: attempted and verified.
    # unknown: attempted, result not verified; the caller must not blindly retry.
    side_effects: Literal["none", "committed", "unknown"] = "none"
    recoveries: list[str] = []
    warnings: list[str] = []
    handoffs: int = 0


# -- helpers ---------------------------------------------------------------

TEMPLATE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def render(template: str | None, values: dict[str, str]) -> str | None:
    if template is None:
        return None
    return TEMPLATE.sub(lambda m: values[m.group(1)], template)


def parse_value(kind: str, raw: str) -> Any:
    """Parse UI text into a declared type. Raises ValueError rather than
    returning something the caller would have to second-guess."""
    text = raw.strip()
    if kind == "string":
        if not text:
            raise ValueError("empty string")
        return text
    cleaned = text.replace("$", "").replace(",", "")
    try:
        if kind == "integer":
            return int(cleaned)
        if kind == "money":
            if not re.fullmatch(r"-?\d+(\.\d{1,2})?", cleaned):
                raise InvalidOperation
            return str(Decimal(cleaned).quantize(Decimal("0.01")))
    except (ValueError, InvalidOperation):
        pass
    raise ValueError(f"{raw!r} is not {kind}")


def check_param(name: str, spec: Param, value: str) -> str | None:
    try:
        parse_value(spec.type, value)
    except ValueError as e:
        return f"{name}: {e}"
    if spec.pattern and not re.fullmatch(spec.pattern, value):
        return f"{name}: does not match {spec.pattern}"
    return None


def load(model: type[Model], path: str | Path) -> Any:
    return model.model_validate(yaml.safe_load(Path(path).read_text()))


class _Flow(dict):
    """A mapping written on one line, so a locator reads as one thought."""


yaml.SafeDumper.add_representer(
    _Flow, lambda dumper, data: dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True))


def _tidy(obj: Any, key: str = "") -> Any:
    """Drop empty values and put locators in flow style, for reviewers."""
    if isinstance(obj, dict):
        items = {k: _tidy(v, k) for k, v in obj.items()}
        return {k: v for k, v in items.items() if v not in (None, "", [], {})}
    if isinstance(obj, list):
        return [_Flow(_tidy(v)) if key == "locators" else _tidy(v) for v in obj]
    return obj


def dump(obj: Model, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(yaml.safe_dump(_tidy(obj.model_dump(mode="json")), sort_keys=False, width=110))
