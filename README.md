# SprintPilot

SprintPilot is a local-first AI development orchestration system that drives Claude Code CLI sessions through a controlled sprint lifecycle: **PLAN → IMPLEMENT → TEST → VALIDATE → DECIDE → CONTINUE/STOP**.

## Status

**Sprint 0B-1 complete — mock execution only.** Real Claude CLI execution is intentionally disabled. No API calls are made and no billing is incurred. This restriction exists pending Sprint 0C, which will add OS-level sandboxing before real execution is enabled.

## Architecture

SprintPilot consists of seven components:

| Component | File | Role |
|---|---|---|
| Sprint Controller | `sprintpilot/controller.py` | Main orchestrator — drives the sprint lifecycle |
| Claude CLI Adapter | `sprintpilot/adapter.py` | Subprocess wrapper around `claude -p` (mock-only now) |
| Sprint State Machine | `sprintpilot/state_machine.py` | 10-state FSM with 19 explicit transitions |
| Independent Validator | `sprintpilot/validator.py` | Deterministic git-based checks — no Claude calls |
| Decision Engine | `sprintpilot/decision.py` | Stateless rule engine (CONTINUE / RETRY / BLOCKED / APPROVAL_REQUIRED) |
| Evidence Store | `sprintpilot/evidence.py` | Atomic JSON/JSONL filesystem writes |
| Human Approval Gate | `sprintpilot/gate.py` | Blocking stdin prompt for gated actions |

## Sprint States

```
PENDING → READY → RUNNING → VALIDATING → PASS
                     ↕              ↓
                  RETRY ←→  APPROVAL_REQUIRED
                              ↓
                         CANCELLED
                  BLOCKED / FAILED  (terminal)
```

## Safety Model

**Defense-in-depth at three layers:**

1. **Prompt instructions** — Claude is told what it may and may not do
2. **CLI tool restrictions** — `--allowedTools` / `--disallowedTools` flags limit available tools
3. **Controller logic** — the validator independently confirms what actually happened

**OS-level sandbox** (`sandbox-exec` / Docker) is deferred to Sprint 0C.

**Gated actions** — the following always require explicit human approval before proceeding:

| Key | Description |
|---|---|
| `commit` | Git commit |
| `push` | Git push |
| `merge` | Git merge/rebase |
| `deploy` | Production deployment |
| `db_write` | Production database mutation |
| `rm_recursive` | Destructive filesystem operation |
| `cloud` | Cloud resource provisioning |
| `paid_api` | Paid API activation |
| `infra` | Infrastructure modification |
| `credential_change` | Credential modification |

Each approval is scoped to exactly one (action, target) pair. Approving one action does not grant permission for future actions.

## Sprint Contract

Sprints are defined by a JSON contract:

```json
{
  "sprint_id": "fix-bug-001",
  "description": "Fix the add() function in calculator.py",
  "allowed_paths": ["src/calculator.py"],
  "gated_actions": ["commit"],
  "max_retries": 2,
  "max_sprint_budget_usd": 3.0,
  "phases": {
    "implement": {
      "allowed_tools": ["Edit", "Read"],
      "permission_mode": "dontAsk",
      "budget_usd": 1.0,
      "timeout_s": 120,
      "model": "claude-haiku-4-5-20251001"
    }
  }
}
```

A sample contract is at `sprint_contracts/poc_fix_calculator.json`.

## Running Tests

Requires Python 3.11+:

```bash
python -m pytest tests/ -q
```

All 129 tests should pass. No network access, no API keys, no billing.

## Recovery Behavior

SprintPilot is fail-closed on crash recovery:

- **RUNNING** with no execution evidence → **FAILED** (cannot verify what ran)
- **PENDING** on recovery → **FAILED** (cannot verify contract acceptance)
- **RUNNING** with execution evidence → resumes from **VALIDATING**

## Project Status

| Sprint | Status | Description |
|---|---|---|
| 0A | Complete | Environment discovery, architecture design |
| 0B-1 | Complete | Full controller implementation (mock-only), 129 tests |
| 0B-2 | Planned | Real Claude CLI adapter, git worktree, multi-phase |
| 0C | Planned | OS-level sandboxing, real execution approval |
