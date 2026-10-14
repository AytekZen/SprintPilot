"""Tests for the decision engine."""
import pytest
from sprintpilot.decision import decide
from sprintpilot.models import Decision, ValidationReport, ValidationVerdict, ValidationCheck


def make_report(
    verdict: ValidationVerdict = ValidationVerdict.PASS,
    new_commits: int = 0,
    out_of_scope: list[str] | None = None,
    symlink_violations: list[str] | None = None,
    checks: list[ValidationCheck] | None = None,
) -> ValidationReport:
    default_checks = [
        ValidationCheck("no_new_commits", new_commits == 0,
                        "ok" if new_commits == 0 else f"{new_commits} new commits"),
        ValidationCheck("no_symlink_escapes", not symlink_violations, "ok"),
        ValidationCheck("canonical_path_safe", True, "ok"),
        ValidationCheck("baseline_integrity", True, "ok"),
    ]
    return ValidationReport(
        attempt=1,
        verdict=verdict,
        checks=checks or default_checks,
        new_commits=new_commits,
        modified_files=[],
        out_of_scope_files=out_of_scope or [],
        untracked_files=[],
        deleted_files=[],
        symlink_violations=symlink_violations or [],
        baseline_commit="abc",
        current_commit="abc",
        failure_reasons=[],
    )


def test_pass_gives_continue():
    report = make_report(ValidationVerdict.PASS)
    assert decide(report, 0, 2, []) == Decision.CONTINUE


def test_fail_with_retries_gives_retry():
    report = make_report(ValidationVerdict.FAIL)
    assert decide(report, 0, 2, []) == Decision.RETRY


def test_fail_last_retry_gives_retry():
    report = make_report(ValidationVerdict.FAIL)
    assert decide(report, 1, 2, []) == Decision.RETRY


def test_fail_exhausted_gives_failed():
    report = make_report(ValidationVerdict.FAIL)
    assert decide(report, 2, 2, []) == Decision.FAILED


def test_fail_no_retries_gives_failed():
    report = make_report(ValidationVerdict.FAIL)
    assert decide(report, 0, 0, []) == Decision.FAILED


def test_blocked_verdict_gives_blocked():
    report = make_report(ValidationVerdict.BLOCKED)
    assert decide(report, 0, 2, []) == Decision.BLOCKED


def test_out_of_scope_gives_blocked():
    report = make_report(ValidationVerdict.FAIL, out_of_scope=["secret.py"])
    assert decide(report, 0, 2, []) == Decision.BLOCKED


def test_out_of_scope_gives_blocked_even_if_pass():
    report = make_report(ValidationVerdict.PASS, out_of_scope=["bad.py"])
    assert decide(report, 0, 2, []) == Decision.BLOCKED


def test_new_commit_without_gated_action_gives_blocked():
    report = make_report(ValidationVerdict.BLOCKED, new_commits=1)
    assert decide(report, 0, 2, []) == Decision.BLOCKED


def test_new_commit_with_gated_action_gives_approval_required():
    report = make_report(ValidationVerdict.BLOCKED, new_commits=1)
    result = decide(report, 0, 2, ["commit"])
    assert result == Decision.APPROVAL_REQUIRED


def test_symlink_violation_gives_blocked():
    report = make_report(ValidationVerdict.BLOCKED, symlink_violations=["link/escape"])
    checks = [
        ValidationCheck("no_symlink_escapes", False, "violation"),
        ValidationCheck("no_new_commits", True, "ok"),
        ValidationCheck("canonical_path_safe", True, "ok"),
        ValidationCheck("baseline_integrity", True, "ok"),
    ]
    report.checks = checks
    assert decide(report, 0, 2, []) == Decision.BLOCKED


def test_timeout_blocked_verdict_gives_blocked():
    report = make_report(ValidationVerdict.BLOCKED)
    assert decide(report, 0, 5, []) == Decision.BLOCKED
