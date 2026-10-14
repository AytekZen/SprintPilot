"""Independent worktree validator. Never trusts model-generated PASS declarations."""
from __future__ import annotations
import os
import subprocess
from pathlib import Path

from .models import (
    ExecutionResult,
    ValidationCheck,
    ValidationReport,
    ValidationVerdict,
)

NON_RETRIABLE_CHECK_NAMES = frozenset({
    "no_new_commits",
    "no_symlink_escapes",
    "canonical_path_safe",
})


class ValidatorError(Exception):
    pass


class WorktreeValidator:
    def __init__(
        self,
        worktree_path: Path,
        allowed_paths: list[str],
        baseline_commit: str,
    ) -> None:
        self._worktree = worktree_path.resolve()
        if not self._worktree.exists():
            raise ValidatorError(f"Worktree path does not exist: {self._worktree}")
        self._allowed_paths = allowed_paths
        self._baseline_commit = baseline_commit

    def validate(self, execution_result: ExecutionResult) -> ValidationReport:
        checks: list[ValidationCheck] = []

        checks.append(self._check_timeout(execution_result))
        checks.append(self._check_exit_code(execution_result))
        checks.append(self._check_canonical_paths())

        try:
            new_commits, current_commit = self._count_new_commits()
        except ValidatorError as e:
            checks.append(ValidationCheck("no_new_commits", False, str(e)))
            new_commits = -1
            current_commit = "unknown"
        else:
            checks.append(ValidationCheck(
                "no_new_commits",
                new_commits == 0,
                f"{new_commits} new commit(s) detected" if new_commits > 0 else "No new commits",
            ))

        try:
            modified, out_of_scope = self._check_file_scope()
        except ValidatorError as e:
            checks.append(ValidationCheck("files_in_scope", False, str(e)))
            modified = []
            out_of_scope = []
        else:
            checks.append(ValidationCheck(
                "files_in_scope",
                len(out_of_scope) == 0,
                f"Out-of-scope: {out_of_scope}" if out_of_scope else "All modifications in scope",
            ))

        try:
            untracked = self._get_untracked_files()
            untracked_oos = [f for f in untracked if not self._is_in_scope(f)]
        except ValidatorError as e:
            checks.append(ValidationCheck("no_untracked_outside_scope", False, str(e)))
            untracked = []
            untracked_oos = []
        else:
            checks.append(ValidationCheck(
                "no_untracked_outside_scope",
                len(untracked_oos) == 0,
                f"Untracked outside scope: {untracked_oos}" if untracked_oos
                else "No unexpected untracked files",
            ))

        try:
            deleted = self._get_deleted_files()
            deleted_oos = [f for f in deleted if not self._is_in_scope(f)]
        except ValidatorError as e:
            checks.append(ValidationCheck("no_deletions_outside_scope", False, str(e)))
            deleted = []
            deleted_oos = []
        else:
            checks.append(ValidationCheck(
                "no_deletions_outside_scope",
                len(deleted_oos) == 0,
                f"Deleted outside scope: {deleted_oos}" if deleted_oos
                else "No unexpected deletions",
            ))

        symlink_violations = self._check_symlinks()
        checks.append(ValidationCheck(
            "no_symlink_escapes",
            len(symlink_violations) == 0,
            f"Symlink escapes: {symlink_violations}" if symlink_violations
            else "No symlink escapes",
        ))

        checks.append(self._check_baseline_integrity())
        checks.append(self._check_structured_output(execution_result))

        failed_checks = [c for c in checks if not c.passed]
        failure_reasons = [c.detail for c in failed_checks]

        has_non_retriable = any(
            c.name in NON_RETRIABLE_CHECK_NAMES and not c.passed for c in checks
        )

        if execution_result.timed_out:
            verdict = ValidationVerdict.BLOCKED
            if "Execution timed out" not in failure_reasons:
                failure_reasons.insert(0, "Execution timed out")
        elif has_non_retriable:
            verdict = ValidationVerdict.BLOCKED
        elif failed_checks:
            verdict = ValidationVerdict.FAIL
        else:
            verdict = ValidationVerdict.PASS

        return ValidationReport(
            attempt=execution_result.attempt,
            verdict=verdict,
            checks=checks,
            new_commits=new_commits if new_commits >= 0 else 0,
            modified_files=modified,
            out_of_scope_files=out_of_scope,
            untracked_files=untracked,
            deleted_files=deleted,
            symlink_violations=symlink_violations,
            baseline_commit=self._baseline_commit,
            current_commit=current_commit if new_commits >= 0 else "unknown",
            failure_reasons=failure_reasons,
        )

    def _check_timeout(self, result: ExecutionResult) -> ValidationCheck:
        return ValidationCheck(
            "no_timeout",
            not result.timed_out,
            "Execution timed out" if result.timed_out else "Completed within timeout",
        )

    def _check_exit_code(self, result: ExecutionResult) -> ValidationCheck:
        return ValidationCheck(
            "exit_code_zero",
            result.exit_code == 0,
            f"Exit code {result.exit_code}" if result.exit_code != 0 else "Exit code 0",
        )

    def _check_canonical_paths(self) -> ValidationCheck:
        try:
            self._worktree.resolve()
            return ValidationCheck("canonical_path_safe", True, "Worktree canonical path verified")
        except Exception as e:
            return ValidationCheck("canonical_path_safe", False, str(e))

    def _count_new_commits(self) -> tuple[int, str]:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=str(self._worktree),
        )
        if result.returncode != 0:
            raise ValidatorError(f"git rev-parse HEAD failed: {result.stderr.strip()}")
        current = result.stdout.strip()

        if not self._baseline_commit or self._baseline_commit == "none":
            return 0, current

        result2 = subprocess.run(
            ["git", "rev-list", "--count", f"{self._baseline_commit}..HEAD"],
            capture_output=True, text=True, cwd=str(self._worktree),
        )
        if result2.returncode != 0:
            raise ValidatorError(f"git rev-list count failed: {result2.stderr.strip()}")
        count = int(result2.stdout.strip())
        return count, current

    def _get_git_status_porcelain(self) -> list[tuple[str, str]]:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            capture_output=True, text=True, cwd=str(self._worktree),
        )
        if result.returncode != 0:
            raise ValidatorError(f"git status failed: {result.stderr.strip()}")
        entries = []
        for line in result.stdout.splitlines():
            if len(line) < 3:
                continue
            xy = line[:2]
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ")[-1]
            entries.append((xy, path.strip()))
        return entries

    def _check_file_scope(self) -> tuple[list[str], list[str]]:
        entries = self._get_git_status_porcelain()
        modified = []
        out_of_scope = []
        for xy, path in entries:
            if xy.strip() in ("??", "!!"):
                continue
            if "D" in xy:
                continue
            modified.append(path)
            if not self._is_in_scope(path):
                out_of_scope.append(path)
        return modified, out_of_scope

    def _get_untracked_files(self) -> list[str]:
        entries = self._get_git_status_porcelain()
        return [path for xy, path in entries if xy == "??"]

    def _get_deleted_files(self) -> list[str]:
        entries = self._get_git_status_porcelain()
        return [path for xy, path in entries if "D" in xy and xy.strip() != "??"]

    def _is_in_scope(self, filepath: str) -> bool:
        if not self._allowed_paths:
            return False
        try:
            abs_path = (self._worktree / filepath).resolve()
            abs_str = str(abs_path)
            worktree_str = str(self._worktree)
            if not abs_str.startswith(worktree_str):
                return False
            for allowed in self._allowed_paths:
                allowed_abs = str((self._worktree / allowed).resolve())
                if abs_str == allowed_abs or abs_str.startswith(allowed_abs + os.sep):
                    return True
            return False
        except Exception:
            return False

    def _check_symlinks(self) -> list[str]:
        violations = []
        worktree_str = str(self._worktree)
        try:
            for p in self._worktree.rglob("*"):
                if p.is_symlink():
                    try:
                        link_target = os.readlink(str(p))
                        if os.path.isabs(link_target):
                            target_resolved = Path(link_target).resolve()
                        else:
                            target_resolved = (p.parent / link_target).resolve()
                        if not str(target_resolved).startswith(worktree_str):
                            violations.append(str(p.relative_to(self._worktree)))
                    except Exception as e:
                        violations.append(f"{p.relative_to(self._worktree)} (error: {e})")
        except Exception:
            pass
        return violations

    def _check_baseline_integrity(self) -> ValidationCheck:
        if not self._baseline_commit or self._baseline_commit == "none":
            return ValidationCheck("baseline_integrity", True, "No baseline commit to verify")
        result = subprocess.run(
            ["git", "cat-file", "-e", self._baseline_commit],
            capture_output=True, cwd=str(self._worktree),
        )
        if result.returncode != 0:
            return ValidationCheck(
                "baseline_integrity", False,
                f"Baseline commit {self._baseline_commit!r} not found in worktree history",
            )
        return ValidationCheck(
            "baseline_integrity", True,
            f"Baseline commit {self._baseline_commit[:8]} verified",
        )

    def _check_structured_output(self, result: ExecutionResult) -> ValidationCheck:
        if result.structured is not None and not isinstance(result.structured, dict):
            return ValidationCheck(
                "structured_output_valid", False,
                "structured output is not a dict",
            )
        return ValidationCheck(
            "structured_output_valid", True,
            "Structured output valid or absent",
        )
