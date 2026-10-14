# SprintPilot

Local-first AI development sprint orchestration controller.

## Project Structure

- `sprintpilot/` — core library
- `sprint_contracts/` — sprint contract JSON files
- `tests/` — pytest test suite
- `evidence/` — runtime evidence (auto-created, gitignored)

## Sprint Status

- Sprint 0A: COMPLETE (READY_FOR_SPRINT_0B)
- Sprint 0B-1: IMPLEMENTATION_WITH_MOCK_EXECUTION

## Development

```bash
python -m pytest tests/ -v
```

## Safety Rules

- Real Claude CLI execution is disabled until Sprint 0C
- No git commit, push, merge without human approval gate
- All test execution uses disposable workspaces in tmp_path
- Validator is authoritative for PASS/FAIL — model self-report is evidence only
- Recovery is fail-closed: interrupted RUNNING state defaults to FAILED/BLOCKED
