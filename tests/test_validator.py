"""Tests for WorktreeValidator — covers scenarios 6-10, 13, 19."""
import os
import subprocess
import pytest
from pathlib import Path

from sprintpilot.validator import WorktreeValidator, ValidatorError
from sprintpilot.models import AdapterMode, ExecutionResult, ValidationVerdict


GIT_ENV = {
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@t.local",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@t.local",
}


def make_result(
    exit_code: int = 0,
    timed_out: bool = False,
    attempt: int = 1,
    structured=None,
) -> ExecutionResult:
    return ExecutionResult(
        attempt=attempt,
        session_id="test-session",
        exit_code=exit_code,
        stdout="",
        stderr="",
        structured=structured,
        wall_time_s=0.1,
        timed_out=timed_out,
        budget_used_usd=0.05,
        tool_calls=[],
        adapter_mode=AdapterMode.MOCK,
    )


def init_repo(path: Path) -> str:
    """Init a git repo and return HEAD sha."""
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.local"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True, capture_output=True)
    (path / "src").mkdir(exist_ok=True)
    (path / "src" / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True, env=env)
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip()


def test_pass_on_clean_in_scope_change(tmp_path):
    baseline = init_repo(tmp_path)
    (tmp_path / "src" / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    v = WorktreeValidator(tmp_path, ["src/calculator.py"], baseline)
    report = v.validate(make_result())
    assert report.verdict == ValidationVerdict.PASS
    assert report.modified_files == ["src/calculator.py"]
    assert report.out_of_scope_files == []


def test_out_of_scope_modification(tmp_path):
    """Scenario 6: modification outside allowed_paths → BLOCKED."""
    baseline = init_repo(tmp_path)
    (tmp_path / "src" / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    v = WorktreeValidator(tmp_path, [], baseline)  # empty allowed_paths
    report = v.validate(make_result())
    assert report.out_of_scope_files == ["src/calculator.py"]
    assert report.verdict in (ValidationVerdict.BLOCKED, ValidationVerdict.FAIL)


def test_untracked_file_outside_scope(tmp_path):
    """Scenario 7: new untracked file outside allowed scope."""
    baseline = init_repo(tmp_path)
    (tmp_path / "hack.py").write_text("import os; os.system('rm -rf /')\n")
    v = WorktreeValidator(tmp_path, ["src/calculator.py"], baseline)
    report = v.validate(make_result())
    assert "hack.py" in report.untracked_files
    # untracked outside scope → FAIL (retriable) since check name is not non-retriable
    assert report.verdict in (ValidationVerdict.FAIL, ValidationVerdict.BLOCKED)


def test_untracked_file_in_scope_is_ok(tmp_path):
    """An untracked file within allowed_paths should not fail."""
    baseline = init_repo(tmp_path)
    (tmp_path / "src" / "helper.py").write_text("x = 1\n")
    v = WorktreeValidator(tmp_path, ["src/helper.py"], baseline)
    report = v.validate(make_result())
    assert "src/helper.py" in report.untracked_files
    assert "src/helper.py" not in report.out_of_scope_files


def test_file_deletion_outside_scope(tmp_path):
    """Scenario 8: deletion of a tracked file outside allowed scope."""
    baseline = init_repo(tmp_path)
    (tmp_path / "src" / "calculator.py").unlink()
    v = WorktreeValidator(tmp_path, [], baseline)
    report = v.validate(make_result())
    assert "src/calculator.py" in report.deleted_files
    assert report.verdict in (ValidationVerdict.FAIL, ValidationVerdict.BLOCKED)


def test_symlink_escape_detected(tmp_path):
    """Scenario 9: symlink pointing outside worktree → BLOCKED."""
    baseline = init_repo(tmp_path)
    outside = tmp_path.parent / "outside_target.txt"
    outside.write_text("secret")
    link = tmp_path / "src" / "escape_link"
    os.symlink(str(outside), str(link))
    v = WorktreeValidator(tmp_path, ["src/calculator.py"], baseline)
    report = v.validate(make_result())
    assert len(report.symlink_violations) > 0
    assert report.verdict == ValidationVerdict.BLOCKED


def test_unexpected_commit_blocked(tmp_path):
    """Scenario 10: new commit in worktree → BLOCKED."""
    baseline = init_repo(tmp_path)
    env = {**os.environ, **GIT_ENV}
    (tmp_path / "src" / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], capture_output=True, env=env)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "unexpected"],
        capture_output=True, env=env,
    )
    v = WorktreeValidator(tmp_path, ["src/calculator.py"], baseline)
    report = v.validate(make_result())
    assert report.new_commits == 1
    assert report.verdict == ValidationVerdict.BLOCKED


def test_timeout_gives_blocked(tmp_path):
    baseline = init_repo(tmp_path)
    v = WorktreeValidator(tmp_path, ["src/calculator.py"], baseline)
    report = v.validate(make_result(timed_out=True))
    assert report.verdict == ValidationVerdict.BLOCKED
    assert any("timed out" in r.lower() for r in report.failure_reasons)


def test_exit_code_nonzero_gives_fail(tmp_path):
    baseline = init_repo(tmp_path)
    v = WorktreeValidator(tmp_path, ["src/calculator.py"], baseline)
    report = v.validate(make_result(exit_code=1))
    assert report.verdict == ValidationVerdict.FAIL


def test_filesystem_isolation(tmp_path):
    """Scenario 19: validator does not write any files outside worktree."""
    baseline = init_repo(tmp_path)
    outside = tmp_path.parent
    files_before = set(outside.iterdir())
    v = WorktreeValidator(tmp_path, ["src/calculator.py"], baseline)
    v.validate(make_result())
    files_after = set(outside.iterdir())
    new_files = files_after - files_before
    # Only the worktree dir itself should be there, no new files from validator
    assert all(f == tmp_path for f in new_files), f"Validator created unexpected files: {new_files}"


def test_missing_evidence_detected(tmp_path):
    """Scenario 13: worktree with no git history (no baseline)."""
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text("x = 1\n")
    # No git init → validator should raise ValidatorError or fail gracefully
    v = WorktreeValidator(tmp_path, ["src/calc.py"], "none")
    result = make_result()
    # git status will fail on non-git dir → checks should mark as FAIL/BLOCKED
    report = v.validate(result)
    # Verdict must not be PASS when git is unavailable
    assert report.verdict != ValidationVerdict.PASS


def test_baseline_commit_not_found(tmp_path):
    init_repo(tmp_path)
    v = WorktreeValidator(tmp_path, [], "deadbeef1234567890deadbeef1234567890dead")
    report = v.validate(make_result())
    # baseline_integrity check fails → BLOCKED
    assert report.verdict == ValidationVerdict.BLOCKED


def test_no_baseline_commit_is_ok(tmp_path):
    init_repo(tmp_path)
    v = WorktreeValidator(tmp_path, ["src/calculator.py"], "none")
    report = v.validate(make_result())
    # With "none" baseline, no new-commit check → PASS (clean repo, exit 0)
    assert report.verdict == ValidationVerdict.PASS


def test_invalid_structured_output(tmp_path):
    baseline = init_repo(tmp_path)
    v = WorktreeValidator(tmp_path, [], baseline)
    result = make_result(structured=[1, 2, 3])  # list not dict
    report = v.validate(result)
    failed = [c for c in report.checks if c.name == "structured_output_valid"]
    assert len(failed) == 1
    assert not failed[0].passed
