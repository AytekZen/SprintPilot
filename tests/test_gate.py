"""Tests for ApprovalGate — covers scenarios 14 (approval) and 15 (rejection)."""
import pytest
from sprintpilot.gate import ApprovalGate, GateDeniedError, UnknownGatedActionError
from sprintpilot.audit import AuditLogger
from sprintpilot.evidence import EvidenceStore


def make_gate(responses: list[str], tmp_path, sprint_id="g-test"):
    store = EvidenceStore(tmp_path, sprint_id)
    audit = AuditLogger(store)
    response_iter = iter(responses)

    def input_fn(prompt: str) -> str:
        return next(response_iter, "")

    return ApprovalGate(audit_logger=audit, input_fn=input_fn), audit


def test_approval_required_flow(tmp_path):
    """Scenario 14: operator approves → returns GateResponse."""
    gate, audit = make_gate(["yes", "", "alice"], tmp_path)
    response = gate.request_approval(
        sprint_id="g-test",
        action="commit",
        target="/tmp/wt",
        context={"attempt": 1},
    )
    assert response.approved is True
    assert response.operator == "alice"
    assert response.request_action == "commit"


def test_approval_rejected_flow(tmp_path):
    """Scenario 15: operator rejects → GateDeniedError raised."""
    gate, audit = make_gate(["no", "wrong branch", "bob"], tmp_path)
    with pytest.raises(GateDeniedError) as exc_info:
        gate.request_approval(
            sprint_id="g-test",
            action="push",
            target="/tmp/wt",
            context={},
        )
    err = exc_info.value
    assert err.action == "push"
    assert "bob" in err.reason or "Rejected" in str(err)


def test_unknown_action_raises(tmp_path):
    gate, _ = make_gate([], tmp_path)
    with pytest.raises(UnknownGatedActionError):
        gate.request_approval("g-test", "unknown_action", "/tmp", {})


def test_approval_logged_to_audit(tmp_path):
    gate, audit = make_gate(["yes", "", "charlie"], tmp_path)
    gate.request_approval("g-test", "deploy", "/tmp", {"env": "prod"})
    events = audit.read_all()
    gate_events = [e for e in events if e.event_type == "gate_decision"]
    assert len(gate_events) == 1
    assert gate_events[0].detail["action"] == "deploy"
    assert gate_events[0].detail["approved"] is True
    assert gate_events[0].detail["operator"] == "charlie"


def test_rejection_logged_to_audit(tmp_path):
    gate, audit = make_gate(["no", "reason", "dave"], tmp_path)
    with pytest.raises(GateDeniedError):
        gate.request_approval("g-test", "merge", "/tmp", {})
    events = audit.read_all()
    gate_events = [e for e in events if e.event_type == "gate_decision"]
    assert len(gate_events) == 1
    assert gate_events[0].detail["approved"] is False


def test_each_approval_is_independent(tmp_path):
    """One approval does not pre-approve the next request."""
    # First approval: yes
    gate1, audit = make_gate(["yes", "", "alice", "no", "", "alice"], tmp_path, "g-test")
    gate1.request_approval("g-test", "commit", "/t", {})
    # Second call on same gate with rejection responses
    gate2, audit2 = make_gate(["no", "", "alice"], tmp_path, "g-test2")
    with pytest.raises(GateDeniedError):
        gate2.request_approval("g-test2", "push", "/t", {})


def test_all_gated_actions_accepted(tmp_path):
    """All valid gated action names are accepted."""
    gated = ["commit", "push", "merge", "deploy", "db_write",
             "rm_recursive", "cloud", "paid_api", "infra", "credential_change"]
    for action in gated:
        gate, _ = make_gate(["yes", "", "op"], tmp_path, f"g-{action}")
        resp = gate.request_approval(f"g-{action}", action, "/t", {})
        assert resp.approved is True


def test_notes_captured(tmp_path):
    gate, _ = make_gate(["yes", "important note here", "op"], tmp_path)
    resp = gate.request_approval("g-test", "infra", "/t", {})
    assert resp.notes == "important note here"


# ── Regression tests for Sprint 0B-1R2 Finding 5.2 ─────────────────────────


def test_commit_approval_with_reason(tmp_path, capsys):
    """Finding 5.2 regression: reason and evidence_file are displayed in the commit gate prompt."""
    gate, _ = make_gate(["yes", "", "alice"], tmp_path)
    response = gate.request_approval(
        sprint_id="g-test",
        action="commit",
        target="/tmp/wt",
        context={"attempt": 1},
        reason="2 new commit(s) detected in worktree (baseline: abc12345)",
        evidence_file="validation_1.json",
    )
    assert response.approved is True
    captured = capsys.readouterr()
    assert "2 new commit(s)" in captured.out
    assert "validation_1.json" in captured.out


def test_push_approval_with_reason(tmp_path, capsys):
    """Finding 5.2 regression: reason and evidence_file are displayed in the push gate prompt."""
    gate, _ = make_gate(["yes", "", "ops-bot"], tmp_path, sprint_id="g-push")
    response = gate.request_approval(
        sprint_id="g-push",
        action="push",
        target="/tmp/repo",
        context={"attempt": 1, "source": "RUNNING"},
        reason="Remote push triggered by execution phase",
        evidence_file="validation_2.json",
    )
    assert response.approved is True
    captured = capsys.readouterr()
    assert "Remote push" in captured.out
    assert "validation_2.json" in captured.out


def test_deploy_approval_with_reason(tmp_path, capsys):
    """Finding 5.2 regression: reason and evidence_file are displayed in the deploy gate prompt."""
    gate, _ = make_gate(["yes", "", "release-eng"], tmp_path, sprint_id="g-deploy")
    response = gate.request_approval(
        sprint_id="g-deploy",
        action="deploy",
        target="prod.cluster",
        context={"env": "production"},
        reason="Production deployment requires operator sign-off",
        evidence_file="validation_1.json",
    )
    assert response.approved is True
    captured = capsys.readouterr()
    assert "Production deployment" in captured.out
    assert "validation_1.json" in captured.out
