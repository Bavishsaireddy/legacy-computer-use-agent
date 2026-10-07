"""Unattended handoff demo: the operator is played by a function, so the evidence can be
regenerated without a person. For a real takeover, use `cua replay ... --operator console --headed`.

The app is made to show an error page; automation gets stuck and requests help. The stand-in
operator does what a person would do in the same browser session (re-opens Member Lookup from
the menu) and answers "retry". Automation then verifies the screen and finishes the flow.
"""

import json
import sys
import urllib.request

from cua.handoff import ScriptedOperator
from cua.replay import replay
from cua.schema import AppProfile, Capability, TenantBinding, load

capability, runs_dir = sys.argv[1], sys.argv[2]
tenant = load(TenantBinding, "tenants/harbor.yaml")
profile = load(AppProfile, f"apps/{tenant.product}.yaml")
urllib.request.urlopen(f"{tenant.base_url}/__fault?error=1").read()

operator = ScriptedOperator(
    "retry", act=lambda page: page.frame(name="nav").get_by_role("link", name="Member Lookup").click())
result = replay(load(Capability, capability), profile, tenant, {"member_id": "10042"},
                operator=operator, runs_dir=runs_dir)
print(json.dumps(result.model_dump(mode="json"), indent=2))
