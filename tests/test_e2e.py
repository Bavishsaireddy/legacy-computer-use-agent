"""End-to-end behaviour against the mock app, with a scripted model in place of the LLM."""

import json
import threading
import time
from pathlib import Path

import pytest

from cua.agent import discover, probe
from cua.handoff import Decision, FileOperator, ScriptedOperator
from cua.replay import replay
from cua.schema import Capability, TenantBinding, dump, load
from cua.surface import ControlError
from conftest import SUBACCT_PARAMS, SUBACCT_SCRIPT, ScriptedLLM, fault, ref
from mockapp import server


def events(runs: str, run_id: str) -> list[dict]:
    return [json.loads(line) for line in (Path(runs) / run_id / "events.jsonl").read_text().splitlines()]


# -- the core loop ---------------------------------------------------------------

def test_artifact_round_trips_and_replays_with_new_input(balance_cap, profile, tenant, runs, tmp_path):
    dump(balance_cap, tmp_path / "cap.yaml")
    cap = load(Capability, tmp_path / "cap.yaml")
    assert cap == balance_cap
    assert cap.steps[1].value == "{{member_id}}"  # parameterised, not the example value
    assert "10042" not in (tmp_path / "cap.yaml").read_text()
    result = replay(cap, profile, tenant, {"member_id": "10077"}, runs_dir=runs)
    assert result.status == "success"
    assert result.outputs == {"savings_balance": "58210.00"}
    assert result.side_effects == "none"


def test_bad_parameter_is_refused_before_touching_the_app(balance_cap, profile, tenant, runs):
    result = replay(balance_cap, profile, tenant, {"member_id": "12"}, runs_dir=runs)
    assert (result.status, result.error.kind, result.error.phase) == ("failed", "PARAM_INVALID", "preflight")
    assert not any(e["type"] == "session_opened" for e in events(runs, result.run_id))


# -- business outcomes vs failures -------------------------------------------------

def test_unknown_screen_is_a_failure_until_probe_names_it_an_outcome(balance_cap, profile, tenant, runs):
    balance_cap.steps[2].timeout_ms = 1500
    before = replay(balance_cap, profile, tenant, {"member_id": "99999"}, runs_dir=runs)
    assert (before.status, before.error.kind, before.error.step) == ("failed", "CHECKPOINT_TIMEOUT", "s4")
    assert before.error.retryable and Path(before.error.evidence["screenshot"]).exists()
    assert "No member on file" in Path(before.error.evidence["snapshot"]).read_text()

    model = ScriptedLLM([("report_outcome", lambda s: {
        "name": "MEMBER_NOT_FOUND", "ref": ref(s, 'text "No member on file'), "description": "no such member"})])
    _, outcome = probe(balance_cap, profile, tenant, {"member_id": "99999"}, model, runs_dir=runs)
    assert outcome.name == "MEMBER_NOT_FOUND"
    assert balance_cap.steps[2].outcomes == [outcome]  # scoped to the search step
    assert "{{member_id}}" in balance_cap.targets[outcome.when].locators[0].name

    after = replay(balance_cap, profile, tenant, {"member_id": "88888"}, runs_dir=runs)
    assert (after.status, after.outcome.name) == ("business_outcome", "MEMBER_NOT_FOUND")
    assert after.outcome.message == "No member on file for number 88888."
    assert after.error is None


def test_permission_denied_is_an_app_level_outcome(balance_cap, profile, tenant, runs):
    result = replay(balance_cap, profile, tenant, {"member_id": "20013"}, runs_dir=runs)
    assert (result.status, result.outcome.name) == ("business_outcome", "PERMISSION_DENIED")


# -- recoverable conditions --------------------------------------------------------

@pytest.mark.parametrize("flags, recovered", [
    ({"notice": 1}, "system_notice x1"),
    ({"expire": 1}, "login x2"),
    ({"slow": 2500}, "login x1"),
])
def test_recoverable_conditions(balance_cap, profile, tenant, runs, flags, recovered):
    fault(**flags)
    result = replay(balance_cap, profile, tenant, {"member_id": "10042"}, runs_dir=runs)
    assert result.status == "success", result.error
    assert recovered in result.recoveries
    assert result.outputs == {"savings_balance": "1234.56"}


def test_app_error_is_a_hard_failure_with_evidence(balance_cap, profile, tenant, runs):
    fault(error=1)
    result = replay(balance_cap, profile, tenant, {"member_id": "10042"}, runs_dir=runs)
    assert (result.status, result.error.kind) == ("failed", "APP_ERROR")
    assert result.error.step and result.error.expected
    assert Path(result.error.evidence["screenshot"]).exists()
    assert "HC-5001" in Path(result.error.evidence["snapshot"]).read_text()


def test_layout_drift_falls_back_and_warns(balance_cap, profile, tenant, runs):
    fault(flat=1)  # the lookup form loses its table, so the row-anchored locator no longer matches
    result = replay(balance_cap, profile, tenant, {"member_id": "10042"}, runs_dir=runs)
    assert result.status == "success", result.error
    assert any("drift" in w and "textbox" in w for w in result.warnings)


# -- irreversible actions ------------------------------------------------------------

def test_discovery_needs_operator_approval_for_irreversible_step(profile, tenant, runs):
    cap, run_id, stopped = discover("open a sub-account", "x", SUBACCT_PARAMS, profile, tenant,
                                    ScriptedLLM(SUBACCT_SCRIPT[:10] + [("give_up", lambda s: {"reason": "denied"})]),
                                    operator=None, runs_dir=runs)
    assert cap is None and stopped == "denied"
    assert any(e["type"] == "intervention_requested" and e["kind"] == "approval" for e in events(runs, run_id))
    assert server.STATE.members["10042"]["subs"] == []


def test_irreversible_capability_is_gated_then_commits_once(subacct_cap, profile, tenant, runs):
    assert [s.risk for s in subacct_cap.steps if s.risk == "irreversible"] == ["irreversible"]
    assert len(server.STATE.members["10042"]["subs"]) == 1  # discovery itself opened one, with approval
    params = {**SUBACCT_PARAMS, "member_id": "10077"}

    draft = replay(subacct_cap, profile, tenant, params, confirm=True, runs_dir=runs)
    assert (draft.status, draft.error.kind) == ("failed", "APPROVAL_REQUIRED")
    subacct_cap.status = "approved"
    unconfirmed = replay(subacct_cap, profile, tenant, params, runs_dir=runs)
    assert unconfirmed.error.kind == "APPROVAL_REQUIRED"
    assert server.STATE.members["10077"]["subs"] == []

    result = replay(subacct_cap, profile, tenant, params, confirm=True, runs_dir=runs)
    assert (result.status, result.side_effects) == ("success", "committed")
    assert result.outputs["confirmation_number"].startswith("SA-")
    assert len(server.STATE.members["10077"]["subs"]) == 1


def test_policy_overrides_a_step_mislabelled_safe(subacct_cap, profile, tenant, runs):
    subacct_cap.status = "approved"
    next(s for s in subacct_cap.steps if s.risk == "irreversible").risk = "safe"
    result = replay(subacct_cap, profile, tenant, {**SUBACCT_PARAMS, "member_id": "10077"}, confirm=True, runs_dir=runs)
    assert (result.status, result.error.kind) == ("failed", "APPROVAL_REQUIRED")
    assert server.STATE.members["10077"]["subs"] == []


def test_lost_response_after_commit_is_never_retried(subacct_cap, profile, tenant, runs):
    subacct_cap.status = "approved"
    fault(lost=1)
    result = replay(subacct_cap, profile, tenant, {**SUBACCT_PARAMS, "member_id": "10077"}, confirm=True, runs_dir=runs)
    assert (result.status, result.side_effects) == ("needs_human", "unknown")
    assert result.error.kind == "APP_ERROR" and not result.error.retryable
    assert len(server.STATE.members["10077"]["subs"]) == 1


def test_unverified_commit_goes_to_a_human_who_is_not_offered_retry(subacct_cap, profile, tenant, runs):
    subacct_cap.status = "approved"
    fault(lost=1)
    operator = ScriptedOperator("abort")
    result = replay(subacct_cap, profile, tenant, {**SUBACCT_PARAMS, "member_id": "10077"}, confirm=True,
                    operator=operator, runs_dir=runs)
    assert operator.requests[0].options == ["done", "abort"]
    assert (result.status, result.side_effects) == ("needs_human", "unknown")


def test_session_expiry_at_commit_does_not_restart_the_flow(subacct_cap, profile, tenant, runs):
    subacct_cap.status = "approved"
    fault(expire_confirm=1)  # the app drops the session as the commit is clicked
    result = replay(subacct_cap, profile, tenant, {**SUBACCT_PARAMS, "member_id": "10077"}, confirm=True, runs_dir=runs)
    # Signing on again and re-running would risk a second posting, so the engine stops instead.
    assert (result.status, result.error.kind, result.side_effects) == ("needs_human", "UNSAFE_RESTART", "unknown")
    assert not result.error.retryable


# -- guardrails ----------------------------------------------------------------------

def test_agent_cannot_leave_the_allowlist(profile, tenant, runs):
    script = [
        ("click", lambda s: {"ref": ref(s, 'link "Admin Console"'), "reason": "explore"}),
        ("click", lambda s: {"ref": ref(s, 'link "Vendor Support"'), "reason": "explore"}),
        ("click", lambda s: {"ref": ref(s, 'link "Sign Off"'), "reason": "explore"}),
    ]
    model = ScriptedLLM(script)
    cap, run_id, stopped = discover("wander", "x", {}, profile, tenant, model, runs_dir=runs)
    assert cap is None and "stuck" in stopped  # three refusals in a row is "stuck", with nobody to ask
    log = events(runs, run_id)
    assert sum(e["type"] == "policy_blocked" for e in log) == 3
    assert "path=/home" in model.screens[-1]  # the session never moved


def test_logs_and_artifacts_hold_no_sensitive_values(balance_cap, profile, tenant, runs, tmp_path):
    result = replay(balance_cap, profile, tenant, {"member_id": "10042"}, runs_dir=runs)
    assert result.outputs == {"savings_balance": "1234.56"}  # the caller gets the real value
    fault(error=1)
    failed = replay(balance_cap, profile, tenant, {"member_id": "10042"}, runs_dir=runs)
    dump(balance_cap, tmp_path / "cap.yaml")
    written = (tmp_path / "cap.yaml").read_text() + "".join(
        p.read_text() for p in Path(runs).rglob("*") if p.suffix in (".jsonl", ".json", ".txt"))
    assert failed.status == "failed"
    for secret in ("10042", server.OPERATOR[1], "1234.56", "1,234.56", "123-45-6789"):
        assert secret not in written, secret
    assert "{{member_id}}" in written and "[output:savings_balance]" in written


# -- human handoff ---------------------------------------------------------------------

def test_human_takes_over_the_same_session_and_automation_resumes(balance_cap, profile, tenant, runs):
    fault(error=1)  # the first main-frame page errors: automation is stuck before step 2
    seen = {}

    class Human:
        """Stands in for a person at the operator console, driving the same page."""

        def handle(self, request, surface):
            seen["request"] = request
            seen["owner"] = surface.lease.owner
            with pytest.raises(ControlError):  # automation is locked out while the human holds the lease
                surface.act("click", balance_cap.targets["link_member_lookup"], {})
            surface.page.frame(name="nav").get_by_role("link", name="Member Lookup").click()
            surface.wait(300)
            return Decision("retry", by="pat")

    result = replay(balance_cap, profile, tenant, {"member_id": "10042"}, operator=Human(), runs_dir=runs)
    assert (result.status, result.handoffs) == ("success", 1)
    assert result.outputs == {"savings_balance": "1234.56"}
    request = seen["request"]
    assert seen["owner"] == "human" and request.step == "s2" and "APP_ERROR" in request.reason
    assert Path(request.screenshot).exists() and request.options == ["retry", "done", "abort"]

    log = events(runs, result.run_id)
    actions = next(e for e in log if e["type"] == "human_actions")["actions"]
    assert actions == [{"kind": "click", "tag": "a", "frame": "nav", "path": "/nav", "label": "Member Lookup"}]
    owners = [(t["from"], t["to"]) for t in next(e for e in log if e["type"] == "control_history")["transfers"]]
    assert owners == [("automation", "human"), ("human", "automation")]


def test_operator_abort_and_timeout(balance_cap, profile, tenant, runs):
    fault(error=1)
    aborted = replay(balance_cap, profile, tenant, {"member_id": "10042"}, operator=ScriptedOperator("abort"), runs_dir=runs)
    assert (aborted.status, aborted.error.kind, aborted.handoffs) == ("failed", "APP_ERROR", 1)
    fault(error=1)
    timed_out = replay(balance_cap, profile, tenant, {"member_id": "10042"}, operator=ScriptedOperator("timeout"), runs_dir=runs)
    assert timed_out.status == "needs_human"


def test_file_queue_operator_publishes_a_request_and_reads_the_decision(balance_cap, profile, tenant, runs, tmp_path):
    """The seam the web console uses: a request file appears, a decision file answers it."""
    queue = tmp_path / "pending"
    seen = {}

    def console():  # plays the console process: waits for a request, then answers it
        while not (requests := [p for p in queue.glob("*.json") if "decision" not in p.name]):
            time.sleep(0.05)
        seen.update(json.loads(requests[0].read_text()))
        (queue / f"{seen['id']}.decision.json").write_text(json.dumps({"action": "abort", "by": "pat"}))

    fault(error=1)
    threading.Thread(target=console, daemon=True).start()
    result = replay(balance_cap, profile, tenant, {"member_id": "10042"},
                    operator=FileOperator(queue, timeout_s=20), runs_dir=runs)
    assert (result.status, result.handoffs) == ("failed", 1)
    assert seen["options"] == ["retry", "done", "abort"] and "APP_ERROR" in seen["reason"]
    assert list(queue.iterdir()) == []  # nothing left waiting
    log = events(runs, result.run_id)
    assert next(e for e in log if e["type"] == "control_transferred" and e["to"] == "automation")["by"] == "pat"


# -- a second tenant running the same product ---------------------------------------------

def test_same_artifact_runs_on_another_tenant_via_overrides(balance_cap, profile, tenant, runs):
    server.LABELS = server.VARIANTS["lakeside"]  # same product, relabelled
    balance_cap.steps[0].timeout_ms = 1500
    plain = replay(balance_cap, profile, tenant, {"member_id": "10042"}, runs_dir=runs)
    assert (plain.status, plain.error.kind, plain.error.step) == ("failed", "CHECKPOINT_TIMEOUT", "s2")

    lakeside = load(TenantBinding, "tenants/lakeside.yaml").model_copy(
        update={"base_url": tenant.base_url, "secrets_env": tenant.secrets_env})
    result = replay(balance_cap, profile, lakeside, {"member_id": "10042"}, runs_dir=runs)
    assert result.status == "success", result.error
    assert result.outputs == {"savings_balance": "1234.56"}
