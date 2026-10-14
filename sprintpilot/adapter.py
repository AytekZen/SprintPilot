"""Claude CLI adapter interface. Real execution disabled in Sprint 0B-1."""
from __future__ import annotations
import json
import os
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .models import AdapterMode, ExecutionResult, PhaseConfig, _utcnow


class RealAdapterDisabledError(Exception):
    """Raised when real Claude CLI execution is attempted before Sprint 0C approval."""


class AuthModeError(Exception):
    """Raised when authentication mode is incompatible with requested execution."""


class AuthMode(str, Enum):
    SUBSCRIPTION = "SUBSCRIPTION"
    API_KEY = "API_KEY"
    UNKNOWN = "UNKNOWN"


def detect_auth_mode() -> AuthMode:
    """
    Detect Claude authentication mode from environment.
    API_KEY mode indicates potential additional billing beyond subscription.
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY"):
        return AuthMode.API_KEY
    return AuthMode.UNKNOWN


class AdapterBase:
    mode: AdapterMode

    def execute(
        self,
        prompt: str,
        phase_config: PhaseConfig,
        worktree_path: Path,
        attempt: int,
    ) -> ExecutionResult:
        raise NotImplementedError


@dataclass
class MockConfig:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    budget_used_usd: float = 0.05
    wall_time_s: float = 0.1
    files_to_write: dict[str, str] = field(default_factory=dict)
    should_create_commit: bool = False
    structured_output: dict | None = None
    tool_calls: list[dict] = field(default_factory=list)


class MockAdapter(AdapterBase):
    mode = AdapterMode.MOCK

    def __init__(self, config: MockConfig | None = None) -> None:
        self._config = config or MockConfig()

    def execute(
        self,
        prompt: str,
        phase_config: PhaseConfig,
        worktree_path: Path,
        attempt: int,
    ) -> ExecutionResult:
        cfg = self._config
        for rel_path, content in cfg.files_to_write.items():
            target = worktree_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        if cfg.should_create_commit:
            import subprocess
            subprocess.run(
                ["git", "-C", str(worktree_path), "add", "-A"],
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(worktree_path), "commit", "-m", "mock: unexpected commit",
                 "--author", "MockAdapter <mock@sprintpilot.local>"],
                capture_output=True,
                env={**os.environ,
                     "GIT_COMMITTER_NAME": "MockAdapter",
                     "GIT_COMMITTER_EMAIL": "mock@sprintpilot.local"},
            )

        stdout = cfg.stdout
        if cfg.structured_output is not None:
            stdout = json.dumps(cfg.structured_output)

        return ExecutionResult(
            attempt=attempt,
            session_id=str(uuid.uuid4()),
            exit_code=cfg.exit_code,
            stdout=stdout,
            stderr=cfg.stderr,
            structured=cfg.structured_output,
            wall_time_s=cfg.wall_time_s,
            timed_out=cfg.timed_out,
            budget_used_usd=cfg.budget_used_usd,
            tool_calls=list(cfg.tool_calls),
            adapter_mode=AdapterMode.MOCK,
        )


class RealAdapter(AdapterBase):
    mode = AdapterMode.REAL

    def execute(self, *args, **kwargs) -> ExecutionResult:
        raise RealAdapterDisabledError(
            "Real Claude CLI execution is disabled in Sprint 0B-1. "
            "REAL mode requires explicit Sprint 0C approval and validated OS-level "
            "security isolation. Use MockAdapter for all Sprint 0B-1 testing."
        )


def create_adapter(
    mode: AdapterMode,
    mock_config: MockConfig | None = None,
) -> AdapterBase:
    """
    Factory for adapters. REAL mode is always rejected in Sprint 0B-1.
    No bypass flag may silently enable real execution.
    """
    if mode == AdapterMode.REAL:
        raise RealAdapterDisabledError(
            "Cannot create RealAdapter: REAL mode is locked until Sprint 0C. "
            "This restriction cannot be bypassed via configuration."
        )
    return MockAdapter(mock_config or MockConfig())
