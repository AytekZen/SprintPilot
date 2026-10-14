"""Integration tests — covers scenarios 3, 4, 5, 20."""
import pytest
from pathlib import Path

from sprintpilot.controller import SprintController
from sprintpilot.adapter import MockAdapter, MockConfig
from sprintpilot.models import SprintContract, PhaseConfig, SprintState
from sprintpilot.evidence import EvidenceStore
from sprintpilot.audit import AuditLogger


def make_contract(sprint_id="integ-001", max_retries=2, allowed_paths=None):
    return SprintContract(
        sprint_id=sprint_id,
        description="Integration test sprint",
        allowed_paths=allowed_paths or ["src/calculator.py"],
        gated_actions=[],
        max_retries=max_retries,
        max_sprint_budget_usd=5.0,
        phases={
            "implement": PhaseConfig(
                name="implement",
                allowed_tools=["Edit", "Read"],
                permission_mode="dontAsk",
                budget_usd=2.0,
                timeout_s=60,
                model="claude-haiku-4-5-20251001",
            )
        },
    )


def run_sprint(contract, workspace, adapter, tmp_path):
    ctrl = SprintController(
        contract=contract,
        workspace_base=workspace,
        adapter=adapter,
        evidence_base=tmp_path / "evidence",
    )
    return ctrl.run(), EvidenceStore(tmp_path / "evidence", contract.sprint_id)


def test_mock_sprint_pass(tmp_path, disposable_git_repo):
    """Scenario 3: mock adapter fixes the file → PASS."""
    contract = make_contract("integ-pass")
    adapter = MockAdapter(MockConfig(
        exit_code=0,
        files_to_write={"src/calculator.py": "def add(a, b):\n    return a + b\n"},
        budget_used_usd=0.05,
    ))
    final_state, store = run_sprint(contract, disposable_git_repo, adapter, tmp_path)
    assert final_state == SprintState.PASS
    assert store.exists("sprint_contract.json")
    assert store.exists("execution_1.json")
    assert store.exists("validation_1.json")
    val = store.read("validation_1.json")
    assert val["verdict"] == "PASS"


def test_mock_sprint_fail(tmp_path, disposable_git_repo):
    """Scenario 4: mock adapter always fails, retries exhausted → BLOCKED/FAILED."""
    contract = make_contract("integ-fail", max_retries=0)
    adapter = MockAdapter(MockConfig(exit_code=1, stderr="test failed"))
    final_state, store = run_sprint(contract, disposable_git_repo, adapter, tmp_path)
    assert final_state in (SprintState.BLOCKED, SprintState.FAILED)


def test_retry_limit_exhausted(tmp_path, disposable_git_repo):
    """Scenario 5: max_retries=1, adapter always fails → BLOCKED after 2 attempts."""
    contract = make_contract("integ-retry", max_retries=1)
    adapter = MockAdapter(MockConfig(exit_code=1, stderr="always fails"))
    final_state, store = run_sprint(contract, disposable_git_repo, adapter, tmp_path)
    assert final_state in (SprintState.BLOCKED, SprintState.FAILED)
    # Should have at least 2 execution attempts
    assert store.exists("execution_1.json")
    assert store.exists("execution_2.json")


def test_audit_trail_complete(tmp_path, disposable_git_repo):
    """Scenario 20: audit.jsonl contains state_transition events for the full run."""
    contract = make_contract("integ-audit")
    adapter = MockAdapter(MockConfig(
        exit_code=0,
        files_to_write={"src/calculator.py": "def add(a, b):\n    return a + b\n"},
    ))
    final_state, store = run_sprint(contract, disposable_git_repo, adapter, tmp_path)
    assert final_state == SprintState.PASS

    audit = AuditLogger(store)
    events = audit.read_all()
    assert len(events) > 0

    transition_events = [e for e in events if e.event_type == "state_transition"]
    assert len(transition_events) > 0, "No state_transition events in audit log"

    execution_events = [e for e in events if e.event_type == "execution"]
    assert len(execution_events) >= 1, "No execution events in audit log"

    validation_events = [e for e in events if e.event_type == "validation"]
    assert len(validation_events) >= 1, "No validation events in audit log"

    # Verify all required evidence files exist
    files = store.list_files()
    assert "sprint_contract.json" in files
    assert "sprint_state.json" in files
    assert "audit.jsonl" in files
    assert "execution_1.json" in files
    assert "validation_1.json" in files
    assert "git_diff_1.patch" in files


def test_timeout_sprint_blocked(tmp_path, disposable_git_repo):
    """Timeout during execution → BLOCKED."""
    contract = make_contract("integ-timeout", max_retries=0)
    adapter = MockAdapter(MockConfig(timed_out=True, exit_code=0))
    final_state, _ = run_sprint(contract, disposable_git_repo, adapter, tmp_path)
    assert final_state == SprintState.BLOCKED


def test_out_of_scope_modification_blocked(tmp_path, disposable_git_repo):
    """Modification outside allowed_paths → BLOCKED immediately (no retry)."""
    contract = make_contract("integ-oos", max_retries=2, allowed_paths=["src/calculator.py"])
    adapter = MockAdapter(MockConfig(
        exit_code=0,
        # Writes a file NOT in allowed_paths
        files_to_write={"HACK.py": "evil = True\n"},
    ))
    final_state, store = run_sprint(contract, disposable_git_repo, adapter, tmp_path)
    assert final_state == SprintState.BLOCKED
    val = store.read("validation_1.json")
    assert val is not None
    assert val["out_of_scope_files"] != [] or val["verdict"] in ("BLOCKED", "FAIL")


def test_sprint_contract_persisted_as_evidence(tmp_path, disposable_git_repo):
    contract = make_contract("integ-evidence")
    adapter = MockAdapter(MockConfig(
        exit_code=0,
        files_to_write={"src/calculator.py": "def add(a, b):\n    return a + b\n"},
    ))
    _, store = run_sprint(contract, disposable_git_repo, adapter, tmp_path)
    saved = store.read("sprint_contract.json")
    assert saved is not None
    assert saved["sprint_id"] == "integ-evidence"


def test_no_commit_or_push_during_sprint(tmp_path, disposable_git_repo):
    """Real git history of the source repo must not change during sprint execution."""
    import subprocess
    r0 = subprocess.run(
        ["git", "-C", str(disposable_git_repo), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True,
    )
    commit_count_before = int(r0.stdout.strip())

    contract = make_contract("integ-nocommit")
    adapter = MockAdapter(MockConfig(
        exit_code=0,
        files_to_write={"src/calculator.py": "def add(a, b):\n    return a + b\n"},
    ))
    run_sprint(contract, disposable_git_repo, adapter, tmp_path)

    r1 = subprocess.run(
        ["git", "-C", str(disposable_git_repo), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True,
    )
    commit_count_after = int(r1.stdout.strip())
    assert commit_count_after == commit_count_before, (
        f"Source repo gained commits: {commit_count_before} → {commit_count_after}"
    )


# ── Regression test for Sprint 0B-1R2 Finding 5.1 ──────────────────────────


def test_gate_approval_finalizes_without_rerun(tmp_path, disposable_git_repo):
    """Finding 5.1 regression: validation-triggered gate approval reaches PASS without re-running.

    The mock adapter commits inside the worktree, which the validator detects as new_commits > 0.
    With gated_actions=["commit"], the decision engine returns APPROVAL_REQUIRED.
    On approval, the sprint must finalize (PASS) and execution_1.json must not be overwritten.
    """
    contract = SprintContract(
        sprint_id="integ-gate",
        description="Gate approval finalization test",
        allowed_paths=["src/calculator.py"],
        gated_actions=["commit"],
        max_retries=0,
        max_sprint_budget_usd=5.0,
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

    approve_responses = iter(["yes", "", "test-approver"])
    gate_fn = lambda prompt: next(approve_responses)

    ctrl = SprintController(
        contract=contract,
        workspace_base=disposable_git_repo,
        adapter=adapter,
        evidence_base=tmp_path / "evidence",
        gate_input_fn=gate_fn,
    )
    final_state = ctrl.run()

    store = EvidenceStore(tmp_path / "evidence", "integ-gate")
    assert final_state == SprintState.PASS
    assert store.exists("execution_1.json"), "execution_1.json must be preserved"
    assert not store.exists("execution_2.json"), "no re-run should occur after terminal approval"
    assert store.exists("gate_request.json"), "gate approval must be recorded"
    gate_data = store.read("gate_request.json")
    assert gate_data["action"] == "commit"
    assert gate_data["source"] == "VALIDATING"
