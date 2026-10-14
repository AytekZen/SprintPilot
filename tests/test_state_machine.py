"""Tests for SprintStateMachine — covers scenario 11 (invalid transition)."""
import pytest
from sprintpilot.state_machine import SprintStateMachine, IllegalTransitionError
from sprintpilot.models import SprintState, TERMINAL_STATES


def make_machine(sprint_id="sm-test") -> SprintStateMachine:
    return SprintStateMachine.create(
        sprint_id=sprint_id,
        worktree_path="/tmp/wt",
        baseline_commit="abc123",
        phase="implement",
    )


def test_create_starts_in_pending():
    m = make_machine()
    assert m.state == SprintState.PENDING


def test_valid_transition_pending_to_ready():
    m = make_machine()
    result = m.transition("contract_valid")
    assert result == SprintState.READY
    assert m.state == SprintState.READY


def test_valid_full_happy_path():
    m = make_machine()
    m.transition("contract_valid")
    m.transition("start_execution")
    m.transition("execution_complete")
    m.transition("all_checks_pass")
    assert m.state == SprintState.PASS
    assert m.state in TERMINAL_STATES


def test_invalid_transition_raises(scenario="11_invalid_transition"):
    """Scenario 11: illegal state transition is rejected."""
    m = make_machine()
    # PENDING cannot receive execution_complete
    with pytest.raises(IllegalTransitionError):
        m.transition("execution_complete")


def test_invalid_event_from_ready():
    m = make_machine()
    m.transition("contract_valid")
    with pytest.raises(IllegalTransitionError):
        m.transition("all_checks_pass")


def test_transition_from_terminal_raises():
    m = make_machine()
    m.transition("contract_valid")
    m.transition("start_execution")
    m.transition("execution_complete")
    m.transition("all_checks_pass")
    assert m.state == SprintState.PASS
    with pytest.raises(IllegalTransitionError):
        m.transition("contract_valid")


def test_terminal_states_are_truly_terminal():
    for terminal in TERMINAL_STATES:
        m = SprintStateMachine.create("x", "/tmp", "none", "p")
        m._data.state = terminal
        assert not m.can_transition("contract_valid")
        with pytest.raises(IllegalTransitionError):
            m.transition("contract_valid")


def test_retry_increments_counter():
    m = make_machine()
    m.transition("contract_valid")
    m.transition("start_execution")
    m.transition("execution_complete")
    m.transition("checks_fail_retry")
    assert m.state == SprintState.RETRY
    m.transition("retry_scheduled")
    assert m.retry_count == 1
    assert m.state == SprintState.RUNNING


def test_attempt_increments_on_retry():
    m = make_machine()
    assert m.attempt == 1
    m.transition("contract_valid")
    m.transition("start_execution")
    m.transition("execution_complete")
    m.transition("checks_fail_retry")
    m.transition("retry_scheduled")
    assert m.attempt == 2


def test_to_dict_roundtrip():
    m = make_machine()
    m.transition("contract_valid")
    m.transition("start_execution")
    d = m.to_dict()
    assert d["state"] == "RUNNING"
    assert d["sprint_id"] == "sm-test"
    assert d["retry_count"] == 0
    assert d["attempt"] == 1
    assert len(d["history"]) == 2


def test_from_dict_restores_state():
    m = make_machine()
    m.transition("contract_valid")
    d = m.to_dict()
    m2 = SprintStateMachine.from_dict(d)
    assert m2.state == SprintState.READY
    assert m2.sprint_id == "sm-test"
    assert len(m2._data.history) == 1


def test_can_transition_returns_false_for_invalid():
    m = make_machine()
    assert not m.can_transition("all_checks_pass")
    assert m.can_transition("contract_valid")


def test_add_budget_accumulates():
    m = make_machine()
    m.add_budget(0.10)
    m.add_budget(0.05)
    assert abs(m.cumulative_budget_usd - 0.15) < 1e-9


def test_detail_recorded_in_history():
    m = make_machine()
    m.transition("contract_valid", {"extra": "info"})
    assert m._data.history[0]["detail"] == {"extra": "info"}


def test_approval_flow():
    m = make_machine()
    m.transition("contract_valid")
    m.transition("start_execution")
    m.transition("execution_complete")
    m.transition("gate_trigger")
    assert m.state == SprintState.APPROVAL_REQUIRED
    m.transition("human_reject")
    assert m.state == SprintState.CANCELLED
    assert m.state in TERMINAL_STATES


def test_approval_then_continue():
    m = make_machine()
    m.transition("contract_valid")
    m.transition("start_execution")
    m.transition("execution_complete")
    m.transition("gate_trigger")
    m.transition("human_approve")
    assert m.state == SprintState.RUNNING


# ── Regression tests for Sprint 0B-1R2 fixes ───────────────────────────────


def test_attempt_increments_on_human_approve():
    """Finding 5.1 regression: human_approve must increment attempt to prevent evidence overwrite."""
    m = make_machine()
    m.transition("contract_valid")
    m.transition("start_execution")
    m.transition("approval_trigger_detected")
    assert m.state == SprintState.APPROVAL_REQUIRED
    assert m.attempt == 1
    m.transition("human_approve")
    assert m.state == SprintState.RUNNING
    assert m.attempt == 2


def test_human_approve_terminal_reaches_pass():
    """Finding 5.1 regression: human_approve_terminal (validation-triggered gate) reaches PASS."""
    m = make_machine()
    m.transition("contract_valid")
    m.transition("start_execution")
    m.transition("execution_complete")
    m.transition("gate_trigger")
    assert m.state == SprintState.APPROVAL_REQUIRED
    m.transition("human_approve_terminal")
    assert m.state == SprintState.PASS
    assert m.state in TERMINAL_STATES


def test_gate_context_set_and_cleared():
    """Finding 5.2 regression: gate context fields are stored and cleared after approval."""
    m = make_machine()
    m.transition("contract_valid")
    m.transition("start_execution")
    m.transition("execution_complete")
    m.set_gate_context(
        action="commit",
        source="VALIDATING",
        reason="1 new commit detected",
        evidence_file="validation_1.json",
    )
    assert m.pending_gate_action == "commit"
    assert m.pending_gate_source == "VALIDATING"
    assert m.pending_gate_reason == "1 new commit detected"
    assert m.pending_gate_evidence == "validation_1.json"
    m.transition("gate_trigger")
    m.transition("human_approve_terminal")
    assert m.pending_gate_action == ""
    assert m.pending_gate_source == ""
    assert m.pending_gate_reason == ""
    assert m.pending_gate_evidence == ""
