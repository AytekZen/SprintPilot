"""Decision engine: stateless, rule-based."""
from __future__ import annotations
from .models import Decision, ValidationReport, ValidationVerdict

NON_RETRIABLE_BLOCKED_CHECKS = frozenset({
    "no_new_commits",
    "no_symlink_escapes",
    "canonical_path_safe",
    "baseline_integrity",
})


def decide(
    report: ValidationReport,
    retry_count: int,
    max_retries: int,
    gated_actions: list[str],
) -> Decision:
    """
    Determine next action based on validation report.

    Priority order:
    1. Timeout or non-retriable violation → BLOCKED (or APPROVAL_REQUIRED for commits)
    2. Out-of-scope modifications → BLOCKED
    3. PASS → CONTINUE
    4. FAIL + retries available → RETRY
    5. FAIL + retries exhausted → FAILED
    6. BLOCKED verdict fallback → BLOCKED
    """
    non_retriable_failed = [
        c for c in report.checks
        if c.name in NON_RETRIABLE_BLOCKED_CHECKS and not c.passed
    ]

    if report.verdict == ValidationVerdict.BLOCKED or non_retriable_failed:
        if report.new_commits > 0 and "commit" in gated_actions:
            return Decision.APPROVAL_REQUIRED
        return Decision.BLOCKED

    if report.out_of_scope_files:
        return Decision.BLOCKED

    if report.verdict == ValidationVerdict.PASS:
        return Decision.CONTINUE

    if report.verdict == ValidationVerdict.FAIL:
        if retry_count < max_retries:
            return Decision.RETRY
        return Decision.FAILED

    return Decision.BLOCKED
