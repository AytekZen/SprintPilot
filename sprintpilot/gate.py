"""Human approval gate for gated actions."""
from __future__ import annotations
from typing import Callable

from .models import GateRequest, GateResponse, _utcnow
from .audit import AuditLogger


GATED_ACTIONS: dict[str, str] = {
    "commit": "Git commit",
    "push": "Git push",
    "merge": "Git merge/rebase",
    "deploy": "Production deployment",
    "db_write": "Production database mutation",
    "rm_recursive": "Destructive filesystem operation",
    "cloud": "Cloud resource provisioning",
    "paid_api": "Paid API activation",
    "infra": "Infrastructure modification",
    "credential_change": "Credential modification",
}


class GateDeniedError(Exception):
    def __init__(self, action: str, reason: str = "Operator rejected") -> None:
        self.action = action
        self.reason = reason
        super().__init__(f"Gate denied for action {action!r}: {reason}")


class UnknownGatedActionError(Exception):
    pass


class ApprovalGate:
    def __init__(
        self,
        audit_logger: AuditLogger,
        input_fn: Callable[[str], str] | None = None,
    ) -> None:
        self._logger = audit_logger
        self._input = input_fn if input_fn is not None else input

    def request_approval(
        self,
        sprint_id: str,
        action: str,
        target: str,
        context: dict,
        reason: str = "",
        evidence_file: str = "",
    ) -> GateResponse:
        if action not in GATED_ACTIONS:
            raise UnknownGatedActionError(
                f"Unknown gated action {action!r}. Valid: {sorted(GATED_ACTIONS)}"
            )

        request = GateRequest(
            sprint_id=sprint_id,
            action=action,
            target=target,
            context=context,
            reason=reason,
            evidence_file=evidence_file,
        )

        label = GATED_ACTIONS[action]
        print(f"\n{'='*60}")
        print("SPRINTPILOT — HUMAN APPROVAL REQUIRED")
        print(f"{'='*60}")
        print(f"Sprint ID     : {sprint_id}")
        print(f"Action        : {label} ({action})")
        print(f"Target        : {target}")
        if reason:
            print(f"Reason        : {reason}")
        if evidence_file:
            print(f"Evidence file : {evidence_file}")
        if context:
            for k, v in context.items():
                print(f"  {k}: {v}")
        print("\nApproval of this action does NOT grant permission for future actions.")
        print("Type 'yes' to approve or 'no' to reject.")
        print(f"{'='*60}")

        decision_raw = self._input("Decision [yes/no]: ").strip().lower()
        notes_raw = self._input("Notes (optional): ").strip()
        operator = self._input("Operator name/ID: ").strip() or "unknown"

        approved = decision_raw == "yes"

        response = GateResponse(
            request_action=action,
            request_target=target,
            sprint_id=sprint_id,
            approved=approved,
            operator=operator,
            decision_timestamp=_utcnow(),
            notes=notes_raw,
        )

        self._logger.log_gate(sprint_id, request, response)

        if not approved:
            raise GateDeniedError(
                action,
                reason=f"Rejected by {operator}: {notes_raw or 'no reason given'}",
            )

        return response
