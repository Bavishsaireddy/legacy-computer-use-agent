"""Guardrails. Enforced inside the surface, so discovery and replay share one choke point."""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from urllib.parse import urlsplit

from .schema import Policy, Risk


class PolicyViolation(Exception):
    """The action or navigation is outside the allowlist."""


class ApprovalRequired(Exception):
    """The action is irreversible and carries no approval."""


@dataclass
class Approval:
    by: str  # "caller-confirm" or "operator:<id>"
    reason: str = ""


class PolicyGuard:
    def __init__(self, policy: Policy, base_url: str) -> None:
        self.policy = policy
        base = urlsplit(base_url)
        self.origin = (base.scheme, base.netloc)

    def url_allowed(self, url: str) -> bool:
        u = urlsplit(url)
        if u.scheme in ("about", "data", "blob"):
            return True
        if (u.scheme, u.netloc) != self.origin:
            return False
        return any(fnmatchcase(u.path or "/", glob) for glob in self.policy.allowed_paths)

    def check_action(self, action: str) -> None:
        if action not in self.policy.allowed_actions:
            raise PolicyViolation(f"action type {action!r} is not allowed")

    def classify(self, action: str, role: str, label: str) -> Risk:
        if action != "click":
            return "safe"  # filling a field commits nothing until a control is clicked
        for rule in self.policy.irreversible:
            if rule.role in (None, role) and re.search(rule.name, label or ""):
                return "irreversible"
        return "safe"
