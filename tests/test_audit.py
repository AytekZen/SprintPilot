"""Tests for AuditLogger."""
import pytest
from sprintpilot.audit import AuditLogger
from sprintpilot.evidence import EvidenceStore
from sprintpilot.models import AuditEvent, GateRequest, GateResponse


@pytest.fixture
def logger(tmp_path):
    store = EvidenceStore(tmp_path, "audit-test")
    return AuditLogger(store)


def test_log_and_read_all(logger):
    event = AuditEvent(
        sprint_id="audit-test",
        event_type="state_transition",
        from_state="PENDING",
        to_state="READY",
        detail={"event": "contract_valid"},
    )
    logger.log(event)
    events = logger.read_all()
    assert len(events) == 1
    assert events[0].event_type == "state_transition"
    assert events[0].from_state == "PENDING"
    assert events[0].to_state == "READY"


def test_log_transition(logger):
    logger.log_transition("audit-test", "READY", "RUNNING", "start_execution")
    events = logger.read_all()
    assert events[0].detail["event"] == "start_execution"


def test_log_transition_with_detail(logger):
    logger.log_transition("audit-test", "RUNNING", "VALIDATING", "execution_complete",
                          detail={"attempt": 1})
    events = logger.read_all()
    assert events[0].detail.get("attempt") == 1


def test_log_execution(logger):
    logger.log_execution("audit-test", {
        "adapter_mode": "MOCK",
        "exit_code": 0,
        "timed_out": False,
        "attempt": 1,
    })
    events = logger.read_all()
    assert events[0].event_type == "execution"
    assert events[0].detail["adapter_mode"] == "MOCK"


def test_log_validation(logger):
    logger.log_validation("audit-test", {
        "verdict": "PASS",
        "attempt": 1,
        "failure_reasons": [],
    })
    events = logger.read_all()
    assert events[0].event_type == "validation"
    assert events[0].detail["verdict"] == "PASS"


def test_log_gate(logger):
    req = GateRequest(sprint_id="audit-test", action="commit", target="/wt", context={})
    resp = GateResponse(
        request_action="commit",
        request_target="/wt",
        sprint_id="audit-test",
        approved=True,
        operator="alice",
        decision_timestamp="2026-01-01T00:00:00Z",
    )
    logger.log_gate("audit-test", req, resp)
    events = logger.read_all()
    assert events[0].event_type == "gate_decision"
    assert events[0].detail["approved"] is True


def test_multiple_events_appended(logger):
    for i in range(5):
        logger.log_transition("audit-test", "A", "B", f"event_{i}")
    events = logger.read_all()
    assert len(events) == 5


def test_read_empty_log(logger):
    events = logger.read_all()
    assert events == []


def test_recovery_flag(logger):
    logger.log_transition("audit-test", "RUNNING", "FAILED", "crash",
                          detail={"recovery": True}, recovery=True)
    events = logger.read_all()
    assert events[0].recovery is True


def test_append_only_no_overwrite(logger):
    logger.log_transition("audit-test", "A", "B", "e1")
    logger.log_transition("audit-test", "B", "C", "e2")
    events = logger.read_all()
    assert len(events) == 2
    assert events[0].detail["event"] == "e1"
    assert events[1].detail["event"] == "e2"
