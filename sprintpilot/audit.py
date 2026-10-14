"""Append-only audit logger."""
from __future__ import annotations
import json
import threading

from .models import AuditEvent, GateRequest, GateResponse
from .evidence import EvidenceStore


class AuditLogger:
    def __init__(self, evidence_store: EvidenceStore) -> None:
        self._store = evidence_store
        self._path = evidence_store.sprint_dir / "audit.jsonl"
        self._lock = threading.Lock()

    def log(self, event: AuditEvent) -> None:
        line = json.dumps(event.to_dict(), default=str) + "\n"
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line)

    def log_transition(
        self,
        sprint_id: str,
        from_state: str | None,
        to_state: str | None,
        event: str,
        detail: dict | None = None,
        recovery: bool = False,
    ) -> None:
        self.log(AuditEvent(
            sprint_id=sprint_id,
            event_type="state_transition",
            from_state=from_state,
            to_state=to_state,
            detail={"event": event, **(detail or {})},
            recovery=recovery,
        ))

    def log_execution(self, sprint_id: str, execution_result_dict: dict) -> None:
        self.log(AuditEvent(
            sprint_id=sprint_id,
            event_type="execution",
            from_state=None,
            to_state=None,
            detail={
                "adapter_mode": execution_result_dict.get("adapter_mode"),
                "exit_code": execution_result_dict.get("exit_code"),
                "timed_out": execution_result_dict.get("timed_out"),
                "attempt": execution_result_dict.get("attempt"),
            },
        ))

    def log_validation(self, sprint_id: str, report_dict: dict) -> None:
        self.log(AuditEvent(
            sprint_id=sprint_id,
            event_type="validation",
            from_state=None,
            to_state=None,
            detail={
                "verdict": report_dict.get("verdict"),
                "attempt": report_dict.get("attempt"),
                "failure_reasons": report_dict.get("failure_reasons", []),
            },
        ))

    def log_gate(
        self,
        sprint_id: str,
        request: GateRequest,
        response: GateResponse,
    ) -> None:
        self.log(AuditEvent(
            sprint_id=sprint_id,
            event_type="gate_decision",
            from_state=None,
            to_state=None,
            detail={
                "action": request.action,
                "target": request.target,
                "approved": response.approved,
                "operator": response.operator,
                "notes": response.notes,
            },
        ))

    def read_all(self) -> list[AuditEvent]:
        if not self._path.exists():
            return []
        events = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                events.append(AuditEvent(
                    sprint_id=d["sprint_id"],
                    event_type=d["event_type"],
                    from_state=d.get("from_state"),
                    to_state=d.get("to_state"),
                    detail=d.get("detail", {}),
                    timestamp=d.get("timestamp", ""),
                    recovery=d.get("recovery", False),
                ))
        return events
