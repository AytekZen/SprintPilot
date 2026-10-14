"""Tests for SprintController — covers scenarios 12 (interrupted), 18 (no destructive git)."""
import json
import os
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, call

from sprintpilot.controller import SprintController, PreconditionError
from sprintpilot.adapter import MockAdapter, MockConfig, RealAdapter, RealAdapterDisabledError
from sprintpilot.models import SprintState, SprintContract, PhaseConfig, AdapterMode
from sprintpilot.state_machine import SprintStateMachine
from sprintpilot.evidence import EvidenceStore


@pytest.fixture
def simple_contract():
    return SprintContract(
        sprint_id="ctrl-001",
        description="Controller test sprint",
        allowed_paths=["src/calculator.py"],
        gated_actions=[],
        max_retries=1,
        max_sprint_budget_usd=3.0,
        phases={
            "implement": PhaseConfig(
                name="implement",
                allowed_tools=["Edit"],
                permission_mode="dontAsk",
                budget_usd=1.0,
                timeout_s=60,
                model="claude-haiku-4-5-20251001",
            )
        },
    )


def make_controller(contract, workspace, adapter, evidence_base, gate_fn=None):
    return SprintController(
        contract=contract,
        workspace_base=workspace,
        adapter=adapter,
        evidence_base=evidence_base,
        gate_input_fn=gate_fn,
    )


def test_recovery_from_running_state_blocked(tmp_path, simple_contract):
    """Scenario 12: RUNNING state with no execution evidence → FAILED (fail-closed)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    evidence_base = tmp_path / "evidence"
    adapter = MockAdapter(MockConfig())

    # Pre-populate sprint_state.json in RUNNING state
    store = EvidenceStore(evidence_base, simple_contract.sprint_id)
    machine = SprintStateMachine.create("ctrl-001", str(workspace), "none", "implement")
    machine.transition("contract_valid")
    machine.transition("start_execution")
    assert machine.state == SprintState.RUNNING
    store.write("sprint_state.json", machine.to_dict())
    # Do NOT write execution_1.json → no evidence

    ctrl = make_controller(simple_contract, workspace, adapter, evidence_base)
    final_state = ctrl.run()
    # Fail-closed: should reach FAILED (from crash transition)
    assert final_state == SprintState.FAILED


def test_recovery_from_running_with_evidence(tmp_path, simple_contract, disposable_git_repo):
    """RUNNING state + execution evidence → re-validate, may reach PASS or BLOCKED."""
    workspace = disposable_git_repo
    evidence_base = tmp_path / "evidence"
    adapter = MockAdapter(MockConfig(
        files_to_write={"src/calculator.py": "def add(a, b):\n    return a + b\n"},
    ))

    store = EvidenceStore(evidence_base, simple_contract.sprint_id)

    # Simulate a completed execution
    exec_result = {
        "attempt": 1,
        "session_id": "test-uuid",
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "structured": None,
        "wall_time_s": 0.1,
        "timed_out": False,
        "budget_used_usd": 0.05,
        "tool_calls": [],
        "adapter_mode": "MOCK",
        "timestamp": "2026-01-01T00:00:00Z",
    }

    # Build RUNNING state
    machine = SprintStateMachine.create("ctrl-001", str(workspace), "none", "implement")
    machine.transition("contract_valid")
    machine.transition("start_execution")

    # We need a worktree that the validator can use; create one matching the path
    worktree_name = f"worktree_ctrl-001_attempt_1"
    worktree_path = workspace.parent / worktree_name
    import shutil
    if worktree_path.exists():
        shutil.rmtree(str(worktree_path))
    shutil.copytree(str(workspace), str(worktree_path), ignore=shutil.ignore_patterns(".git"))
    subprocess.run(["git", "init", str(worktree_path)], capture_output=True)
    subprocess.run(["git", "-C", str(worktree_path), "config", "user.email", "t@t.local"], capture_output=True)
    subprocess.run(["git", "-C", str(worktree_path), "config", "user.name", "T"], capture_output=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.local",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.local"}
    subprocess.run(["git", "-C", str(worktree_path), "add", "-A"], capture_output=True, env=env)
    subprocess.run(["git", "-C", str(worktree_path), "commit", "-m", "baseline"], capture_output=True, env=env)
    head = subprocess.run(["git", "-C", str(worktree_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()

    machine._data.worktree_path = str(worktree_path)
    machine._data.baseline_commit = head
    exec_result["attempt"] = 1

    store.write("sprint_state.json", machine.to_dict())
    store.write("execution_1.json", exec_result)

    ctrl = make_controller(simple_contract, workspace, adapter, evidence_base)
    final_state = ctrl.run()
    assert final_state in (SprintState.PASS, SprintState.BLOCKED, SprintState.FAILED)


def test_no_destructive_git_commands(tmp_path, simple_contract, disposable_git_repo):
    """Scenario 18: controller never issues git checkout -- . or git reset --hard."""
    workspace = disposable_git_repo
    adapter = MockAdapter(MockConfig(
        files_to_write={"src/calculator.py": "def add(a, b):\n    return a + b\n"},
    ))

    captured_commands = []
    original_run = subprocess.run

    def capture_run(cmd, *args, **kwargs):
        if isinstance(cmd, list):
            captured_commands.append(cmd)
        return original_run(cmd, *args, **kwargs)

    # adapter.py imports subprocess inside the execute() method; patch controller + validator
    with patch("sprintpilot.controller.subprocess.run", side_effect=capture_run):
        with patch("sprintpilot.validator.subprocess.run", side_effect=capture_run):
            ctrl = make_controller(simple_contract, workspace, adapter, tmp_path / "evidence")
            ctrl.run()

    destructive = [
        cmd for cmd in captured_commands
        if "checkout" in cmd and "--" in cmd
        or ("reset" in cmd and "--hard" in cmd)
    ]
    assert destructive == [], f"Destructive git commands found: {destructive}"


def test_real_adapter_rejected_in_auth_preflight(tmp_path, simple_contract):
    """Scenario 16: RealAdapter is blocked by auth preflight."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    adapter = RealAdapter()
    ctrl = make_controller(simple_contract, workspace, adapter, tmp_path / "evidence")
    with pytest.raises(RealAdapterDisabledError):
        ctrl.run()


def test_terminal_state_returns_immediately(tmp_path, simple_contract):
    """If existing state is terminal, controller returns without re-running."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    adapter = MockAdapter()

    store = EvidenceStore(tmp_path / "evidence", simple_contract.sprint_id)
    machine = SprintStateMachine.create("ctrl-001", str(workspace), "none", "implement")
    machine._data.state = SprintState.PASS
    store.write("sprint_state.json", machine.to_dict())

    ctrl = make_controller(simple_contract, workspace, adapter, tmp_path / "evidence")
    final = ctrl.run()
    assert final == SprintState.PASS


def test_nonexistent_workspace_blocked(tmp_path, simple_contract):
    """Precondition failure → FAILED state."""
    workspace = tmp_path / "does_not_exist"
    adapter = MockAdapter()
    ctrl = make_controller(simple_contract, workspace, adapter, tmp_path / "evidence")
    final = ctrl.run()
    assert final == SprintState.FAILED


# ── Regression tests for Sprint 0B-1R2 fixes ───────────────────────────────


def test_recovery_from_pending_fails_closed(tmp_path, simple_contract):
    """Finding 5.3 regression: PENDING state on recovery → FAILED (fail-closed)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    evidence_base = tmp_path / "evidence"
    adapter = MockAdapter(MockConfig())

    # Pre-populate sprint_state.json in PENDING (simulates crash before contract validation)
    store = EvidenceStore(evidence_base, simple_contract.sprint_id)
    machine = SprintStateMachine.create("ctrl-001", str(workspace), "none", "implement")
    assert machine.state == SprintState.PENDING
    store.write("sprint_state.json", machine.to_dict())

    ctrl = make_controller(simple_contract, workspace, adapter, evidence_base)
    final_state = ctrl.run()
    assert final_state == SprintState.FAILED


def test_evidence_preserved_after_gate_approval(tmp_path, disposable_git_repo):
    """Finding 5.1 regression: execution_1.json is not overwritten when gate fires from VALIDATING."""
    contract = SprintContract(
        sprint_id="ctrl-gate",
        description="Gate evidence preservation test",
        allowed_paths=["src/calculator.py"],
        gated_actions=["commit"],
        max_retries=0,
        max_sprint_budget_usd=3.0,
        phases={
            "implement": PhaseConfig(
                name="implement",
                allowed_tools=["Edit"],
                permission_mode="dontAsk",
                budget_usd=1.0,
                timeout_s=60,
                model="claude-haiku-4-5-20251001",
            )
        },
    )
    adapter = MockAdapter(MockConfig(
        exit_code=0,
        files_to_write={"src/calculator.py": "def add(a, b):\n    return a + b\n"},
        should_create_commit=True,
    ))

    approve_responses = iter(["yes", "", "test-op"])
    gate_fn = lambda prompt: next(approve_responses)

    evidence_base = tmp_path / "evidence"
    ctrl = make_controller(contract, disposable_git_repo, adapter, evidence_base, gate_fn)
    final_state = ctrl.run()

    store = EvidenceStore(evidence_base, "ctrl-gate")
    assert final_state == SprintState.PASS
    assert store.exists("execution_1.json"), "execution_1.json must be preserved"
    assert not store.exists("execution_2.json"), "no re-run should occur after terminal approval"


def test_gate_action_derived_from_validation_report():
    """Finding 5.2 regression: gate action is derived from the report, not hardcoded to 'commit'."""
    from sprintpilot.controller import _derive_gate_action, _describe_gate_reason
    from sprintpilot.models import ValidationReport, ValidationVerdict

    report = ValidationReport(
        attempt=1,
        verdict=ValidationVerdict.BLOCKED,
        checks=[],
        new_commits=2,
        modified_files=[],
        out_of_scope_files=[],
        untracked_files=[],
        deleted_files=[],
        symlink_violations=[],
        baseline_commit="abc123456789",
        current_commit="def987654321",
        failure_reasons=["2 new commit(s) detected"],
    )

    action = _derive_gate_action(report, ["commit", "push"])
    assert action == "commit"

    reason = _describe_gate_reason(report)
    assert "2 new commit" in reason
    assert "abc12345" in reason

    # When no commits but gated_actions has a different first action, that action is returned
    report_no_commits = ValidationReport(
        attempt=1,
        verdict=ValidationVerdict.BLOCKED,
        checks=[],
        new_commits=0,
        modified_files=[],
        out_of_scope_files=["hack.py"],
        untracked_files=[],
        deleted_files=[],
        symlink_violations=[],
        baseline_commit="abc123456789",
        current_commit="abc123456789",
        failure_reasons=["out of scope"],
    )
    action2 = _derive_gate_action(report_no_commits, ["push", "deploy"])
    assert action2 == "push"
