"""Sprint Controller — main orchestrator."""
from __future__ import annotations
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .adapter import AdapterBase, AdapterMode, RealAdapterDisabledError, detect_auth_mode, AuthMode
from .audit import AuditLogger
from .decision import decide
from .evidence import EvidenceStore
from .gate import ApprovalGate, GateDeniedError
from .models import (
    Decision,
    ExecutionResult,
    SprintContract,
    SprintState,
    TERMINAL_STATES,
    ValidationVerdict,
)
from .state_machine import SprintStateMachine, IllegalTransitionError
from .validator import WorktreeValidator

logger = logging.getLogger(__name__)


def _derive_gate_action(report: "ValidationReport", gated_actions: list[str]) -> str:
    """Return the most specific gated action type matching the validation findings."""
    if report.new_commits > 0 and "commit" in gated_actions:
        return "commit"
    # Additional heuristics for future action types can be added here (Sprint 0C).
    # Fall back to "commit" as the most common validation-triggered gate.
    for action in gated_actions:
        return action  # first declared gated action
    return "commit"


def _describe_gate_reason(report: "ValidationReport") -> str:
    """Build a human-readable reason string from the validation report."""
    if report.new_commits > 0:
        return f"{report.new_commits} new commit(s) detected in worktree (baseline: {report.baseline_commit[:8]})"
    if report.out_of_scope_files:
        return f"Out-of-scope files modified: {', '.join(report.out_of_scope_files[:3])}"
    if report.failure_reasons:
        return report.failure_reasons[0]
    return "Gated action requires operator approval"


class SprintControllerError(Exception):
    pass


class PreconditionError(SprintControllerError):
    pass


class SprintController:
    def __init__(
        self,
        contract: SprintContract,
        workspace_base: Path,
        adapter: AdapterBase,
        evidence_base: Path,
        gate_input_fn: Callable[[str], str] | None = None,
    ) -> None:
        self._contract = contract
        self._workspace_base = workspace_base.resolve()
        self._adapter = adapter
        self._evidence = EvidenceStore(evidence_base, contract.sprint_id)
        self._audit = AuditLogger(self._evidence)
        self._gate = ApprovalGate(self._audit, input_fn=gate_input_fn)

    def run(self) -> SprintState:
        self._auth_preflight()
        machine, recovered = self._load_or_create_machine()
        self._evidence.write("sprint_contract.json", self._contract.to_dict())
        try:
            return self._drive(machine, recovered)
        except Exception as exc:
            logger.exception("Unexpected controller error")
            self._try_transition(machine, "crash", {"error": str(exc)})
            self._persist_state(machine)
            raise

    def _auth_preflight(self) -> None:
        auth_mode = detect_auth_mode()
        if auth_mode == AuthMode.API_KEY:
            logger.warning(
                "ANTHROPIC_API_KEY detected. Real execution may incur additional billing. "
                "SprintPilot 0B-1 runs in MOCK mode — no real API calls will be made."
            )
        if self._adapter.mode == AdapterMode.REAL:
            raise RealAdapterDisabledError(
                "RealAdapter is disabled in Sprint 0B-1. Auth preflight blocked real execution."
            )

    def _load_or_create_machine(self) -> tuple[SprintStateMachine, bool]:
        existing = self._evidence.read("sprint_state.json")
        if existing is None:
            machine = SprintStateMachine.create(
                sprint_id=self._contract.sprint_id,
                worktree_path=str(self._workspace_base),
                baseline_commit="none",
                phase=next(iter(self._contract.phases)),
            )
            self._persist_state(machine)
            return machine, False

        machine = SprintStateMachine.from_dict(existing)
        if machine.state in TERMINAL_STATES:
            return machine, False

        return self._handle_recovery(machine)

    def _handle_recovery(self, machine: SprintStateMachine) -> tuple[SprintStateMachine, bool]:
        current = machine.state

        if current == SprintState.RUNNING:
            execution_file = f"execution_{machine.attempt}.json"
            if self._evidence.exists(execution_file):
                logger.warning("Recovery: RUNNING with evidence → VALIDATING")
                machine.transition("execution_complete", {"recovery": True})
                self._persist_state(machine)
                self._audit.log_transition(
                    machine.sprint_id, "RUNNING", "VALIDATING",
                    "execution_complete", {"recovery": True}, recovery=True,
                )
                return machine, True
            else:
                logger.warning("Recovery: RUNNING without evidence → FAILED (fail-closed)")
                self._try_transition(machine, "crash", {
                    "recovery": True,
                    "reason": "No execution evidence found",
                })
                self._persist_state(machine)
                self._audit.log_transition(
                    machine.sprint_id, "RUNNING", machine.state.value,
                    "crash", {"recovery": True}, recovery=True,
                )
                return machine, True

        if current == SprintState.PENDING:
            # PENDING is instantaneous; a crash here means we cannot verify
            # whether the contract was accepted. Fail closed.
            logger.warning("Recovery: PENDING state → failing closed (cannot verify contract acceptance)")
            self._try_transition(machine, "contract_invalid", {
                "recovery": True,
                "reason": "Fail-closed recovery from PENDING: contract acceptance unverifiable",
            })
            self._persist_state(machine)
            self._audit.log_transition(
                machine.sprint_id, "PENDING", machine.state.value,
                "contract_invalid", {"recovery": True}, recovery=True,
            )
            return machine, True

        logger.info(f"Recovery: resuming from {current.value}")
        return machine, True

    def _drive(self, machine: SprintStateMachine, recovered: bool) -> SprintState:
        if machine.state in TERMINAL_STATES:
            return machine.state

        if machine.state == SprintState.PENDING and not recovered:
            try:
                self._validate_preconditions()
                machine.transition("contract_valid")
            except PreconditionError as exc:
                machine.transition("contract_invalid", {"error": str(exc)})
                self._persist_state(machine)
                return machine.state
            self._persist_state(machine)

        while machine.state not in TERMINAL_STATES:
            state = machine.state

            if state == SprintState.READY:
                machine.transition("start_execution")
                self._persist_state(machine)

            elif state == SprintState.RUNNING:
                execution_result = self._run_phase(machine)
                self._persist_state(machine)
                if execution_result.timed_out:
                    machine.transition("timeout", {"attempt": machine.attempt})
                else:
                    machine.transition("execution_complete")
                self._persist_state(machine)

            elif state == SprintState.VALIDATING:
                report = self._run_validator(machine)
                decision = decide(
                    report,
                    machine.retry_count,
                    self._contract.max_retries,
                    self._contract.gated_actions,
                )
                self._apply_decision(machine, decision, report)
                self._persist_state(machine)

            elif state == SprintState.RETRY:
                machine.transition("retry_scheduled")
                self._persist_state(machine)

            elif state == SprintState.APPROVAL_REQUIRED:
                self._handle_gate(machine)
                self._persist_state(machine)

            else:
                logger.error(f"Unexpected state in drive loop: {state}")
                break

        return machine.state

    def _validate_preconditions(self) -> None:
        if not self._workspace_base.exists():
            raise PreconditionError(
                f"Workspace base does not exist: {self._workspace_base}"
            )

    def _run_phase(self, machine: SprintStateMachine) -> ExecutionResult:
        phase_name = machine.phase
        phase_config = self._contract.phases[phase_name]
        worktree = self._create_worktree(machine.attempt)
        machine._data.worktree_path = str(worktree)

        baseline = self._get_head_commit(worktree)
        machine._data.baseline_commit = baseline

        prompt = (
            f"Sprint {self._contract.sprint_id}: {self._contract.description}\n"
            f"Phase: {phase_name}\n"
            f"Allowed paths: {', '.join(self._contract.allowed_paths) or '(none)'}\n"
        )

        result = self._adapter.execute(prompt, phase_config, worktree, machine.attempt)
        machine.add_budget(result.budget_used_usd or 0.0)

        self._evidence.write(f"execution_{machine.attempt}.json", result.to_dict())
        self._audit.log_execution(machine.sprint_id, result.to_dict())

        diff = self._capture_git_diff(worktree)
        self._evidence.write_text(f"git_diff_{machine.attempt}.patch", diff)

        return result

    def _run_validator(self, machine: SprintStateMachine) -> "ValidationReport":
        from .models import AdapterMode as AM, ValidationReport
        worktree = Path(machine.worktree_path)
        execution_data = self._evidence.read(f"execution_{machine.attempt}.json")
        if execution_data is None:
            raise SprintControllerError(
                f"Missing execution evidence for attempt {machine.attempt}"
            )

        execution_result = ExecutionResult(
            attempt=execution_data["attempt"],
            session_id=execution_data["session_id"],
            exit_code=execution_data["exit_code"],
            stdout=execution_data["stdout"],
            stderr=execution_data["stderr"],
            structured=execution_data["structured"],
            wall_time_s=execution_data["wall_time_s"],
            timed_out=execution_data["timed_out"],
            budget_used_usd=execution_data["budget_used_usd"],
            tool_calls=execution_data["tool_calls"],
            adapter_mode=AM(execution_data["adapter_mode"]),
            timestamp=execution_data["timestamp"],
        )

        validator = WorktreeValidator(
            worktree_path=worktree,
            allowed_paths=self._contract.allowed_paths,
            baseline_commit=machine.baseline_commit,
        )
        report = validator.validate(execution_result)
        self._evidence.write(f"validation_{machine.attempt}.json", report.to_dict())
        self._audit.log_validation(machine.sprint_id, report.to_dict())
        return report

    def _apply_decision(self, machine: SprintStateMachine, decision: Decision, report) -> None:
        if decision == Decision.CONTINUE:
            machine.transition("all_checks_pass")
        elif decision == Decision.RETRY:
            machine.transition("checks_fail_retry", {"attempt": machine.attempt})
        elif decision == Decision.BLOCKED:
            if report.out_of_scope_files or report.symlink_violations:
                machine.transition("checks_fail_exhausted", {"reason": "Non-retriable violation"})
            elif report.verdict == ValidationVerdict.BLOCKED:
                machine.transition("checks_fail_exhausted", {"reason": "Blocked verdict"})
            else:
                machine.transition("checks_fail_exhausted", {"reason": "Retries exhausted"})
        elif decision == Decision.APPROVAL_REQUIRED:
            # Derive action type from the validation report
            action = _derive_gate_action(report, self._contract.gated_actions)
            reason = _describe_gate_reason(report)
            evidence_file = f"validation_{machine.attempt}.json"
            machine.set_gate_context(
                action=action,
                source="VALIDATING",
                reason=reason,
                evidence_file=evidence_file,
            )
            machine.transition("gate_trigger", {"reason": reason, "action": action})
        elif decision == Decision.FAILED:
            machine.transition("checks_fail_exhausted", {"reason": "Max retries reached"})
        self._audit.log_transition(
            machine.sprint_id,
            "VALIDATING",
            machine.state.value,
            decision.value,
            {"verdict": report.verdict.value},
        )

    def _handle_gate(self, machine: SprintStateMachine) -> None:
        action = machine.pending_gate_action or "commit"
        source = machine.pending_gate_source  # "VALIDATING", "RUNNING", or ""
        reason = machine.pending_gate_reason or "Gated action requires approval"
        evidence_file = machine.pending_gate_evidence or f"validation_{machine.attempt}.json"

        try:
            response = self._gate.request_approval(
                machine.sprint_id,
                action=action,
                target=machine.worktree_path,
                context={"attempt": machine.attempt, "source": source},
                reason=reason,
                evidence_file=evidence_file,
            )
            self._evidence.write("gate_request.json", {
                "action": action,
                "source": source,
                "reason": reason,
                "evidence_file": evidence_file,
                "response": response.to_dict(),
            })
            # Gate from VALIDATING: the work is already done and now approved → PASS
            # Gate from RUNNING (execution-triggered): continue with a new attempt → RUNNING
            if source == "VALIDATING":
                machine.transition("human_approve_terminal", {"operator": response.operator})
            else:
                machine.transition("human_approve", {"operator": response.operator})
        except GateDeniedError as exc:
            machine.transition("human_reject", {"reason": str(exc)})

    def _create_worktree(self, attempt: int) -> Path:
        """
        Create an isolated workspace for this attempt.
        Copies workspace_base to a fresh temp directory.
        Never uses git checkout -- . or git reset --hard.
        """
        worktree_name = f"worktree_{self._contract.sprint_id}_attempt_{attempt}"
        worktree_path = self._workspace_base.parent / worktree_name

        if worktree_path.exists():
            shutil.rmtree(str(worktree_path))

        if self._is_git_repo(self._workspace_base):
            shutil.copytree(
                str(self._workspace_base),
                str(worktree_path),
                symlinks=True,
                ignore=shutil.ignore_patterns(".git"),
            )
            subprocess.run(["git", "init", str(worktree_path)], capture_output=True)
            subprocess.run(
                ["git", "-C", str(worktree_path), "config", "user.email", "sprintpilot@local"],
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(worktree_path), "config", "user.name", "SprintPilot"],
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(worktree_path), "add", "-A"],
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(worktree_path), "commit", "-m", "baseline snapshot",
                 "--author", "SprintPilot <sprintpilot@local>"],
                capture_output=True,
                env={**os.environ,
                     "GIT_COMMITTER_NAME": "SprintPilot",
                     "GIT_COMMITTER_EMAIL": "sprintpilot@local"},
            )
        else:
            shutil.copytree(str(self._workspace_base), str(worktree_path), symlinks=True)

        return worktree_path

    def _is_git_repo(self, path: Path) -> bool:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-dir"],
            capture_output=True,
        )
        return result.returncode == 0

    def _get_head_commit(self, worktree: Path) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=str(worktree),
        )
        if result.returncode != 0:
            return "none"
        return result.stdout.strip()

    def _capture_git_diff(self, worktree: Path) -> str:
        result = subprocess.run(
            ["git", "diff", "--stat"],
            capture_output=True, text=True, cwd=str(worktree),
        )
        if result.returncode != 0:
            return ""
        return result.stdout

    def _try_transition(self, machine: SprintStateMachine, event: str, detail: dict) -> None:
        try:
            machine.transition(event, detail)
        except IllegalTransitionError:
            pass

    def _persist_state(self, machine: SprintStateMachine) -> None:
        self._evidence.write("sprint_state.json", machine.to_dict())
