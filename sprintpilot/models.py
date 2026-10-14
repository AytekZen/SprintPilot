"""Core data models for SprintPilot."""
from __future__ import annotations
import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SprintState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    VALIDATING = "VALIDATING"
    PASS = "PASS"
    RETRY = "RETRY"
    BLOCKED = "BLOCKED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES: frozenset[SprintState] = frozenset({
    SprintState.PASS,
    SprintState.BLOCKED,
    SprintState.FAILED,
    SprintState.CANCELLED,
})

MUTABLE_STATES: frozenset[SprintState] = frozenset({
    SprintState.PENDING,
    SprintState.READY,
    SprintState.RUNNING,
    SprintState.VALIDATING,
    SprintState.RETRY,
    SprintState.APPROVAL_REQUIRED,
})


class Decision(str, Enum):
    CONTINUE = "CONTINUE"
    RETRY = "RETRY"
    BLOCKED = "BLOCKED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    FAILED = "FAILED"


class ValidationVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class AdapterMode(str, Enum):
    MOCK = "MOCK"
    REAL = "REAL"


def _utcnow() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


@dataclass
class PhaseConfig:
    name: str
    allowed_tools: list[str]
    permission_mode: str
    budget_usd: float
    timeout_s: int
    model: str
    effort: str = "medium"
    append_system_prompt: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "allowed_tools": self.allowed_tools,
            "permission_mode": self.permission_mode,
            "budget_usd": self.budget_usd,
            "timeout_s": self.timeout_s,
            "model": self.model,
            "effort": self.effort,
            "append_system_prompt": self.append_system_prompt,
        }

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "PhaseConfig":
        return cls(
            name=name,
            allowed_tools=d["allowed_tools"],
            permission_mode=d["permission_mode"],
            budget_usd=d["budget_usd"],
            timeout_s=d["timeout_s"],
            model=d["model"],
            effort=d.get("effort", "medium"),
            append_system_prompt=d.get("append_system_prompt", ""),
        )


@dataclass
class SprintContract:
    sprint_id: str
    description: str
    allowed_paths: list[str]
    gated_actions: list[str]
    max_retries: int
    max_sprint_budget_usd: float
    phases: dict[str, PhaseConfig]

    def to_dict(self) -> dict:
        return {
            "sprint_id": self.sprint_id,
            "description": self.description,
            "allowed_paths": self.allowed_paths,
            "gated_actions": self.gated_actions,
            "max_retries": self.max_retries,
            "max_sprint_budget_usd": self.max_sprint_budget_usd,
            "phases": {k: v.to_dict() for k, v in self.phases.items()},
        }


@dataclass
class ExecutionResult:
    attempt: int
    session_id: str
    exit_code: int
    stdout: str
    stderr: str
    structured: dict[str, Any] | None
    wall_time_s: float
    timed_out: bool
    budget_used_usd: float | None
    tool_calls: list[dict]
    adapter_mode: AdapterMode
    timestamp: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return {
            "attempt": self.attempt,
            "session_id": self.session_id,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "structured": self.structured,
            "wall_time_s": self.wall_time_s,
            "timed_out": self.timed_out,
            "budget_used_usd": self.budget_used_usd,
            "tool_calls": self.tool_calls,
            "adapter_mode": self.adapter_mode.value,
            "timestamp": self.timestamp,
        }


@dataclass
class ValidationCheck:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class ValidationReport:
    attempt: int
    verdict: ValidationVerdict
    checks: list[ValidationCheck]
    new_commits: int
    modified_files: list[str]
    out_of_scope_files: list[str]
    untracked_files: list[str]
    deleted_files: list[str]
    symlink_violations: list[str]
    baseline_commit: str
    current_commit: str
    failure_reasons: list[str]
    timestamp: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return {
            "attempt": self.attempt,
            "verdict": self.verdict.value,
            "checks": [c.to_dict() for c in self.checks],
            "new_commits": self.new_commits,
            "modified_files": self.modified_files,
            "out_of_scope_files": self.out_of_scope_files,
            "untracked_files": self.untracked_files,
            "deleted_files": self.deleted_files,
            "symlink_violations": self.symlink_violations,
            "baseline_commit": self.baseline_commit,
            "current_commit": self.current_commit,
            "failure_reasons": self.failure_reasons,
            "timestamp": self.timestamp,
        }


@dataclass
class AuditEvent:
    sprint_id: str
    event_type: str
    from_state: str | None
    to_state: str | None
    detail: dict
    timestamp: str = field(default_factory=_utcnow)
    recovery: bool = False

    def to_dict(self) -> dict:
        return {
            "sprint_id": self.sprint_id,
            "event_type": self.event_type,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "detail": self.detail,
            "timestamp": self.timestamp,
            "recovery": self.recovery,
        }


@dataclass
class GateRequest:
    sprint_id: str
    action: str
    target: str
    context: dict
    reason: str = ""        # why this action requires approval
    evidence_file: str = "" # evidence filename backing this request
    timestamp: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return {
            "sprint_id": self.sprint_id,
            "action": self.action,
            "target": self.target,
            "context": self.context,
            "reason": self.reason,
            "evidence_file": self.evidence_file,
            "timestamp": self.timestamp,
        }


@dataclass
class GateResponse:
    request_action: str
    request_target: str
    sprint_id: str
    approved: bool
    operator: str
    decision_timestamp: str
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "request_action": self.request_action,
            "request_target": self.request_target,
            "sprint_id": self.sprint_id,
            "approved": self.approved,
            "operator": self.operator,
            "decision_timestamp": self.decision_timestamp,
            "notes": self.notes,
        }
