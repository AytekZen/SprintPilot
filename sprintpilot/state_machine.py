"""Sprint state machine with explicit transition table."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from .models import SprintState, TERMINAL_STATES, _utcnow

TRANSITION_TABLE: dict[tuple[str, str], str] = {
    ("PENDING", "contract_valid"): "READY",
    ("PENDING", "contract_invalid"): "FAILED",
    ("READY", "start_execution"): "RUNNING",
    ("READY", "precondition_fail"): "BLOCKED",
    ("RUNNING", "execution_complete"): "VALIDATING",
    ("RUNNING", "timeout"): "BLOCKED",
    ("RUNNING", "budget_exhausted"): "BLOCKED",
    ("RUNNING", "approval_trigger_detected"): "APPROVAL_REQUIRED",
    ("RUNNING", "crash"): "FAILED",
    ("VALIDATING", "all_checks_pass"): "PASS",
    ("VALIDATING", "checks_fail_retry"): "RETRY",
    ("VALIDATING", "checks_fail_exhausted"): "BLOCKED",
    ("VALIDATING", "gate_trigger"): "APPROVAL_REQUIRED",
    ("VALIDATING", "validator_crash"): "FAILED",
    ("RETRY", "retry_scheduled"): "RUNNING",
    # human_approve: gate triggered from RUNNING (execution continues with new attempt)
    ("APPROVAL_REQUIRED", "human_approve"): "RUNNING",
    # human_approve_terminal: gate triggered from VALIDATING (work is done, approval finalises)
    ("APPROVAL_REQUIRED", "human_approve_terminal"): "PASS",
    ("APPROVAL_REQUIRED", "human_reject"): "CANCELLED",
    ("APPROVAL_REQUIRED", "timeout_no_response"): "CANCELLED",
}


class IllegalTransitionError(Exception):
    pass


@dataclass
class StateMachineData:
    sprint_id: str
    state: SprintState
    retry_count: int
    attempt: int
    baseline_commit: str
    worktree_path: str
    phase: str
    cumulative_budget_usd: float
    history: list[dict] = field(default_factory=list)
    # Gate context — set when APPROVAL_REQUIRED is entered, cleared on resolution
    pending_gate_action: str = ""    # gated action key (e.g. "commit")
    pending_gate_source: str = ""    # "VALIDATING" or "RUNNING"
    pending_gate_reason: str = ""    # human-readable reason
    pending_gate_evidence: str = ""  # evidence filename reference

    def to_dict(self) -> dict:
        return {
            "sprint_id": self.sprint_id,
            "state": self.state.value,
            "retry_count": self.retry_count,
            "attempt": self.attempt,
            "baseline_commit": self.baseline_commit,
            "worktree_path": self.worktree_path,
            "phase": self.phase,
            "cumulative_budget_usd": self.cumulative_budget_usd,
            "history": self.history,
            "pending_gate_action": self.pending_gate_action,
            "pending_gate_source": self.pending_gate_source,
            "pending_gate_reason": self.pending_gate_reason,
            "pending_gate_evidence": self.pending_gate_evidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StateMachineData":
        return cls(
            sprint_id=d["sprint_id"],
            state=SprintState(d["state"]),
            retry_count=d["retry_count"],
            attempt=d["attempt"],
            baseline_commit=d["baseline_commit"],
            worktree_path=d["worktree_path"],
            phase=d["phase"],
            cumulative_budget_usd=d["cumulative_budget_usd"],
            history=d.get("history", []),
            pending_gate_action=d.get("pending_gate_action", ""),
            pending_gate_source=d.get("pending_gate_source", ""),
            pending_gate_reason=d.get("pending_gate_reason", ""),
            pending_gate_evidence=d.get("pending_gate_evidence", ""),
        )


class SprintStateMachine:
    def __init__(self, data: StateMachineData) -> None:
        self._data = data

    @property
    def state(self) -> SprintState:
        return self._data.state

    @property
    def retry_count(self) -> int:
        return self._data.retry_count

    @property
    def attempt(self) -> int:
        return self._data.attempt

    @property
    def sprint_id(self) -> str:
        return self._data.sprint_id

    @property
    def baseline_commit(self) -> str:
        return self._data.baseline_commit

    @property
    def worktree_path(self) -> str:
        return self._data.worktree_path

    @property
    def cumulative_budget_usd(self) -> float:
        return self._data.cumulative_budget_usd

    @property
    def phase(self) -> str:
        return self._data.phase

    @property
    def pending_gate_action(self) -> str:
        return self._data.pending_gate_action

    @property
    def pending_gate_source(self) -> str:
        return self._data.pending_gate_source

    @property
    def pending_gate_reason(self) -> str:
        return self._data.pending_gate_reason

    @property
    def pending_gate_evidence(self) -> str:
        return self._data.pending_gate_evidence

    def set_gate_context(
        self,
        action: str,
        source: str,
        reason: str,
        evidence_file: str,
    ) -> None:
        self._data.pending_gate_action = action
        self._data.pending_gate_source = source
        self._data.pending_gate_reason = reason
        self._data.pending_gate_evidence = evidence_file

    def transition(self, event: str, detail: dict[str, Any] | None = None) -> SprintState:
        current = self._data.state.value
        if self._data.state in TERMINAL_STATES:
            raise IllegalTransitionError(
                f"Cannot transition from terminal state {current!r} on event {event!r}"
            )
        key = (current, event)
        if key not in TRANSITION_TABLE:
            raise IllegalTransitionError(
                f"No transition from {current!r} on event {event!r}"
            )
        new_state = SprintState(TRANSITION_TABLE[key])
        entry = {
            "from": current,
            "to": new_state.value,
            "event": event,
            "timestamp": _utcnow(),
            "detail": detail or {},
        }
        self._data.history.append(entry)
        self._data.state = new_state
        if event == "retry_scheduled":
            self._data.retry_count += 1
            self._data.attempt += 1
        elif event in ("human_approve", "human_approve_terminal"):
            # human_approve: gate from RUNNING → continue with a fresh attempt
            # human_approve_terminal: gate from VALIDATING → PASS, no new attempt needed
            if event == "human_approve":
                self._data.attempt += 1
            self._data.pending_gate_action = ""
            self._data.pending_gate_source = ""
            self._data.pending_gate_reason = ""
            self._data.pending_gate_evidence = ""
        return new_state

    def can_transition(self, event: str) -> bool:
        if self._data.state in TERMINAL_STATES:
            return False
        return (self._data.state.value, event) in TRANSITION_TABLE

    def add_budget(self, amount: float) -> None:
        self._data.cumulative_budget_usd += amount

    def to_dict(self) -> dict:
        return self._data.to_dict()

    @classmethod
    def from_dict(cls, d: dict) -> "SprintStateMachine":
        return cls(StateMachineData.from_dict(d))

    @classmethod
    def create(
        cls,
        sprint_id: str,
        worktree_path: str,
        baseline_commit: str,
        phase: str,
    ) -> "SprintStateMachine":
        data = StateMachineData(
            sprint_id=sprint_id,
            state=SprintState.PENDING,
            retry_count=0,
            attempt=1,
            baseline_commit=baseline_commit,
            worktree_path=worktree_path,
            phase=phase,
            cumulative_budget_usd=0.0,
            history=[],
        )
        return cls(data)
