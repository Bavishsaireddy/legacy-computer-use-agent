"""What discovery and replay share: an open session against one tenant's app."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .evidence import RunLog
from .policy import PolicyGuard
from .redact import Redactor
from .schema import AppProfile, Target, TenantBinding
from .surface import Surface
from .web import WebSurface


# A snapshot line with no children: `- cell "Caption"` or `- text: words`.
_LEAF = re.compile(r'^-? ?\'?[a-z]+(?: "(.*)")?\'?(?:: (.+))?$')


class ConfigError(Exception):
    pass


@dataclass
class Session:
    surface: Surface
    log: RunLog
    redactor: Redactor
    profile: AppProfile
    values: dict[str, str]  # template values: params plus "secret.<name>"
    targets: dict[str, Target]

    def target(self, name: str) -> Target:
        return self.targets[name]

    def present(self, name: str) -> bool:
        return self.surface.present(self.targets[name], self.values)

    def capture(self, label: str, mask: list[str]) -> dict[str, str]:
        """Failure evidence: a masked screenshot and a redacted accessibility snapshot."""
        shot = str(self.log.dir / f"{label}.png")
        try:
            self.surface.screenshot(shot, [self.targets[m] for m in mask if m in self.targets], self.values)
        except Exception as e:  # evidence must never mask the original failure
            shot = f"unavailable: {e}"
        return {"screenshot": shot, "snapshot": self.log.write_text(f"{label}.a11y.txt", self.surface.dump())}

    def observed(self) -> str:
        """A short, redacted description of every frame, for error messages."""
        frames = []
        for section in self.surface.dump().split("--- frame ")[1:]:
            header, *lines = section.splitlines()
            leaves = []
            for line in lines:
                m = _LEAF.match(line.strip())
                if m and (text := m.group(1) or m.group(2)):
                    leaves.append(text)
            frames.append(f"[{header}] " + " | ".join(leaves)[:220])
        return self.redactor.text("  ".join(frames))[:600]


def start_log(kind: str, name: str, params: dict[str, str], runs_dir: str = "runs") -> RunLog:
    redactor = Redactor()
    for key, value in params.items():
        redactor.add(value, "{{%s}}" % key)
    return RunLog(kind, name, redactor, runs_dir)


def open_session(profile: AppProfile, tenant: TenantBinding, log: RunLog, params: dict[str, str],
                 targets: dict[str, Target], headed: bool = False) -> Session:
    if tenant.product != profile.product:
        raise ConfigError(f"tenant {tenant.tenant} runs {tenant.product}, not {profile.product}")
    values = dict(params)
    for name, env in tenant.secrets_env.items():
        if env not in os.environ:
            raise ConfigError(f"secret {name!r}: environment variable {env} is not set")
        values[f"secret.{name}"] = os.environ[env]
        log.redactor.add(os.environ[env], f"[secret:{name}]")
    merged = {**profile.targets, **targets, **tenant.target_overrides}
    unknown = set(tenant.target_overrides) - set(profile.targets) - set(targets)
    surface = WebSurface(tenant.base_url, PolicyGuard(profile.policy, tenant.base_url), headed=headed)
    log.event("session_opened", tenant=tenant.tenant, product=profile.product,
              origin=urlsplit(tenant.base_url).netloc, overrides=sorted(tenant.target_overrides),
              unused_overrides=sorted(unknown))
    return Session(surface, log, log.redactor, profile, values, merged)
