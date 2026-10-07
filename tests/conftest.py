"""Fixtures: two mock tenants, and a scripted stand-in for the model.

The scripted model lets the whole discover -> compile -> replay path run in
tests without an API key. It is given the same screen text the real model sees
and must pick refs out of it, so the agent loop and compiler are exercised for real.
"""

from __future__ import annotations

import re
import threading
import urllib.request
from types import SimpleNamespace

import pytest

from cua.agent import discover, verify
from cua.handoff import ScriptedOperator
from cua.schema import AppProfile, TenantBinding, load
from mockapp import server

PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="session", autouse=True)
def app():
    srv = server.serve(PORT)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield
    srv.shutdown()


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    urllib.request.urlopen(f"{BASE}/__reset").read()
    server.LABELS = server.VARIANTS["harbor"]
    monkeypatch.setenv("HARBOR_USERNAME", server.OPERATOR[0])
    monkeypatch.setenv("HARBOR_PASSWORD", server.OPERATOR[1])


def fault(**flags) -> None:
    query = "&".join(f"{k}={v}" for k, v in flags.items())
    urllib.request.urlopen(f"{BASE}/__fault?{query}").read()


@pytest.fixture(scope="session")
def profile() -> AppProfile:
    return load(AppProfile, "apps/harborcore.yaml")


@pytest.fixture(scope="session")
def tenant() -> TenantBinding:
    return TenantBinding(tenant="test", product="harborcore", base_url=BASE,
                         secrets_env={"username": "HARBOR_USERNAME", "password": "HARBOR_PASSWORD"})


@pytest.fixture
def runs(tmp_path) -> str:
    return str(tmp_path / "runs")


# -- scripted model ------------------------------------------------------------

def ref(screen: str, pattern: str) -> str:
    """The ref of the first element whose rendered line matches `pattern`."""
    m = re.search(r"\[(e\d+)\] " + pattern, screen)
    assert m, f"no element matching {pattern!r} in:\n{screen}"
    return m.group(1)


def ref_in_row(screen: str, label: str, role: str) -> str:
    """The ref of the first `role` element after the cell captioned `label`."""
    tail = screen.split(f'cell "{label}"', 1)[1]
    return ref(tail, role)


class ScriptedLLM:
    model = "scripted-test-model"

    def __init__(self, script) -> None:
        self.script, self.turn = list(script), 0
        self.screens: list[str] = []

    def complete(self, system, messages, tools):
        last = messages[-1]["content"]
        screen = last if isinstance(last, str) else "\n".join(b["content"] for b in last)
        self.screens.append(screen)
        name, make = self.script[self.turn]
        self.turn += 1
        block = SimpleNamespace(type="tool_use", id=f"t{self.turn}", name=name, input=make(screen))
        return SimpleNamespace(content=[block], stop_reason="tool_use", usage=None)


BALANCE_SCRIPT = [
    ("click", lambda s: {"ref": ref(s, 'link "Member Lookup"'), "reason": "open lookup"}),
    ("type", lambda s: {"ref": ref_in_row(s, "Member Number:", "textbox"), "text": "{{member_id}}", "reason": "enter member"}),
    ("click", lambda s: {"ref": ref(s, 'button "Search"'), "reason": "search"}),
    ("click", lambda s: {"ref": ref(s, 'link "View"'), "reason": "open detail"}),
    ("extract", lambda s: {"ref": ref_in_row(s, "Share Savings Balance:", "cell"), "output": "savings_balance",
                           "type": "money", "reason": "read balance"}),
    ("done", lambda s: {"success_ref": ref_in_row(s, "Share Savings Balance:", "cell"), "summary": "read"}),
]

SUBACCT_SCRIPT = [
    *BALANCE_SCRIPT[:4],
    ("click", lambda s: {"ref": ref(s, 'link "Open Sub-Account"'), "reason": "start"}),
    ("select", lambda s: {"ref": ref_in_row(s, "Account Type:", "combobox"), "option": "{{account_type}}", "reason": "type"}),
    ("type", lambda s: {"ref": ref_in_row(s, "Nickname:", "textbox"), "text": "{{nickname}}", "reason": "nick"}),
    ("type", lambda s: {"ref": ref_in_row(s, "Opening Deposit:", "textbox"), "text": "{{deposit}}", "reason": "deposit"}),
    ("click", lambda s: {"ref": ref(s, 'button "Continue"'), "reason": "review"}),
    ("click", lambda s: {"ref": ref(s, 'button "Confirm and Open Account"'), "reason": "commit"}),
    ("extract", lambda s: {"ref": ref_in_row(s, "Confirmation Number:", "cell"), "output": "confirmation_number",
                           "type": "string", "reason": "read confirmation"}),
    ("done", lambda s: {"success_ref": ref_in_row(s, "Confirmation Number:", "cell"), "summary": "opened"}),
]
SUBACCT_PARAMS = {"member_id": "10042", "account_type": "Holiday Club", "nickname": "Gifts", "deposit": "25.00"}


@pytest.fixture
def balance_cap(profile, tenant, runs):
    cap, _, stopped = discover("read the savings balance", "member.read_savings_balance", {"member_id": "10042"},
                               profile, tenant, ScriptedLLM(BALANCE_SCRIPT), runs_dir=runs)
    assert cap, stopped
    verify(cap, profile, tenant, [{"member_id": "10042"}, {"member_id": "10077"}], runs_dir=runs)
    assert cap.provenance.verified, cap.provenance.verification
    return cap


@pytest.fixture
def subacct_cap(profile, tenant, runs):
    cap, _, stopped = discover("open a sub-account", "member.open_sub_account", SUBACCT_PARAMS, profile, tenant,
                               ScriptedLLM(SUBACCT_SCRIPT), operator=ScriptedOperator("approve"), runs_dir=runs)
    assert cap, stopped
    return cap
