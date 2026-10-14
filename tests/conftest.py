"""Shared fixtures for SprintPilot tests."""
import os
import subprocess
import pytest
from pathlib import Path

from sprintpilot.models import SprintContract, PhaseConfig
from sprintpilot.adapter import MockAdapter, MockConfig
from sprintpilot.evidence import EvidenceStore
from sprintpilot.audit import AuditLogger


GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@sp.local",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@sp.local",
}


@pytest.fixture
def disposable_git_repo(tmp_path):
    """Git repo with a buggy calculator and a test for it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@sp.local"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "SprintPilot Test"],
        check=True, capture_output=True,
    )
    (repo / "src").mkdir()
    (repo / "src" / "calculator.py").write_text(
        "def add(a, b):\n    return a - b\n"
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calculator.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
        "from src.calculator import add\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
    )
    subprocess.run(
        ["git", "-C", str(repo), "add", "."],
        check=True, capture_output=True, env=env,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial commit"],
        check=True, capture_output=True, env=env,
    )
    return repo


@pytest.fixture
def sample_contract():
    return SprintContract(
        sprint_id="test-001",
        description="Test sprint",
        allowed_paths=["src/calculator.py"],
        gated_actions=[],
        max_retries=2,
        max_sprint_budget_usd=3.0,
        phases={
            "implement": PhaseConfig(
                name="implement",
                allowed_tools=["Edit", "Read"],
                permission_mode="dontAsk",
                budget_usd=1.5,
                timeout_s=120,
                model="claude-haiku-4-5-20251001",
            )
        },
    )


@pytest.fixture
def evidence_store(tmp_path):
    return EvidenceStore(tmp_path / "evidence", "test-001")


@pytest.fixture
def audit_logger(evidence_store):
    return AuditLogger(evidence_store)


@pytest.fixture
def mock_adapter_success():
    return MockAdapter(MockConfig(
        exit_code=0,
        files_to_write={"src/calculator.py": "def add(a, b):\n    return a + b\n"},
        budget_used_usd=0.05,
    ))


@pytest.fixture
def mock_adapter_failure():
    return MockAdapter(MockConfig(exit_code=1, stderr="Mock test failure"))


@pytest.fixture
def mock_adapter_timeout():
    return MockAdapter(MockConfig(exit_code=0, timed_out=True))
