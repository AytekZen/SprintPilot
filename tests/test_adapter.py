"""Tests for adapter — covers scenarios 16 (real blocked) and 17 (no creds needed)."""
import os
import pytest
from pathlib import Path

from sprintpilot.adapter import (
    MockAdapter, MockConfig, RealAdapter,
    RealAdapterDisabledError, create_adapter, detect_auth_mode, AuthMode,
)
from sprintpilot.models import AdapterMode, PhaseConfig


@pytest.fixture
def phase_config():
    return PhaseConfig(
        name="implement",
        allowed_tools=["Edit"],
        permission_mode="dontAsk",
        budget_usd=1.0,
        timeout_s=60,
        model="claude-haiku-4-5-20251001",
    )


def test_mock_adapter_returns_result(phase_config, tmp_path):
    adapter = MockAdapter(MockConfig(exit_code=0, stdout="ok"))
    result = adapter.execute("test", phase_config, tmp_path, attempt=1)
    assert result.exit_code == 0
    assert result.stdout == "ok"
    assert result.adapter_mode == AdapterMode.MOCK
    assert result.attempt == 1


def test_real_adapter_raises(phase_config, tmp_path):
    """Scenario 16: real adapter always raises."""
    adapter = RealAdapter()
    with pytest.raises(RealAdapterDisabledError) as exc_info:
        adapter.execute("test", phase_config, tmp_path, attempt=1)
    assert "Sprint 0B-1" in str(exc_info.value)
    assert "Sprint 0C" in str(exc_info.value)


def test_create_adapter_mock_succeeds():
    adapter = create_adapter(AdapterMode.MOCK)
    assert adapter.mode == AdapterMode.MOCK
    assert isinstance(adapter, MockAdapter)


def test_create_adapter_real_raises():
    """Scenario 16: factory rejects REAL mode."""
    with pytest.raises(RealAdapterDisabledError) as exc_info:
        create_adapter(AdapterMode.REAL)
    assert "Sprint 0C" in str(exc_info.value)


def test_mock_needs_no_credentials(phase_config, tmp_path, monkeypatch):
    """Scenario 17: MOCK mode works without any API credentials."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    adapter = MockAdapter()
    result = adapter.execute("prompt", phase_config, tmp_path, attempt=1)
    assert result.adapter_mode == AdapterMode.MOCK


def test_mock_writes_files_to_worktree(phase_config, tmp_path):
    cfg = MockConfig(files_to_write={"src/foo.py": "x = 1\n"})
    adapter = MockAdapter(cfg)
    adapter.execute("p", phase_config, tmp_path, attempt=1)
    assert (tmp_path / "src" / "foo.py").read_text() == "x = 1\n"


def test_mock_timeout_flag(phase_config, tmp_path):
    cfg = MockConfig(timed_out=True, exit_code=0)
    adapter = MockAdapter(cfg)
    result = adapter.execute("p", phase_config, tmp_path, attempt=1)
    assert result.timed_out is True


def test_mock_budget_returned(phase_config, tmp_path):
    cfg = MockConfig(budget_used_usd=0.42)
    result = MockAdapter(cfg).execute("p", phase_config, tmp_path, attempt=1)
    assert result.budget_used_usd == 0.42


def test_mock_structured_output(phase_config, tmp_path):
    cfg = MockConfig(structured_output={"status": "ok"})
    result = MockAdapter(cfg).execute("p", phase_config, tmp_path, attempt=1)
    assert result.structured == {"status": "ok"}


def test_mock_create_commit_in_worktree(phase_config, tmp_path, disposable_git_repo):
    """MockAdapter can optionally create a git commit (for testing detection)."""
    import subprocess
    # Copy repo into tmp worktree-like directory
    worktree = tmp_path / "wt"
    import shutil
    shutil.copytree(str(disposable_git_repo), str(worktree), ignore=shutil.ignore_patterns(".git"))
    subprocess.run(["git", "init", str(worktree)], capture_output=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.email", "t@t.local"], capture_output=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "T"], capture_output=True)
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], capture_output=True)
    env = {**os.environ, "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.local"}
    subprocess.run(["git", "-C", str(worktree), "commit", "-m", "baseline"], capture_output=True, env=env)

    import subprocess as sp
    r0 = sp.run(["git", "-C", str(worktree), "rev-list", "--count", "HEAD"], capture_output=True, text=True)
    count_before = int(r0.stdout.strip())

    cfg = MockConfig(
        files_to_write={"src/calculator.py": "def add(a,b): return a+b\n"},
        should_create_commit=True,
    )
    adapter = MockAdapter(cfg)
    adapter.execute("p", phase_config, worktree, attempt=1)

    r1 = sp.run(["git", "-C", str(worktree), "rev-list", "--count", "HEAD"], capture_output=True, text=True)
    count_after = int(r1.stdout.strip())
    assert count_after > count_before


def test_detect_auth_mode_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    assert detect_auth_mode() == AuthMode.UNKNOWN


def test_detect_auth_mode_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    assert detect_auth_mode() == AuthMode.API_KEY
