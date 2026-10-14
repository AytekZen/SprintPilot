"""Tests for ContractLoader — covers scenarios 1 (valid) and 2 (invalid)."""
import json
import pytest
from pathlib import Path
from sprintpilot.contract import ContractLoader, ContractValidationError


VALID_CONTRACT = {
    "sprint_id": "test-001",
    "description": "A valid test sprint",
    "allowed_paths": ["src/main.py"],
    "gated_actions": [],
    "max_retries": 2,
    "max_sprint_budget_usd": 3.0,
    "phases": {
        "implement": {
            "allowed_tools": ["Edit", "Read"],
            "permission_mode": "dontAsk",
            "budget_usd": 1.5,
            "timeout_s": 120,
            "model": "claude-haiku-4-5-20251001",
        }
    },
}


@pytest.fixture
def loader():
    return ContractLoader()


def test_load_valid_contract(loader):
    """Scenario 1: valid contract loads without error."""
    contract = loader.load_dict(VALID_CONTRACT)
    assert contract.sprint_id == "test-001"
    assert contract.description == "A valid test sprint"
    assert contract.allowed_paths == ["src/main.py"]
    assert contract.max_retries == 2
    assert contract.max_sprint_budget_usd == 3.0
    assert "implement" in contract.phases
    phase = contract.phases["implement"]
    assert phase.permission_mode == "dontAsk"
    assert phase.budget_usd == 1.5
    assert phase.timeout_s == 120


def test_load_dict_valid(loader):
    contract = loader.load_dict(VALID_CONTRACT)
    assert contract.sprint_id == "test-001"


def test_load_from_file(loader, tmp_path):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(VALID_CONTRACT))
    contract = loader.load(path)
    assert contract.sprint_id == "test-001"


def test_missing_required_field(loader):
    """Scenario 2: missing field raises ContractValidationError."""
    data = {k: v for k, v in VALID_CONTRACT.items() if k != "sprint_id"}
    with pytest.raises(ContractValidationError) as exc_info:
        loader.load_dict(data)
    assert "sprint_id" in str(exc_info.value)


def test_missing_description(loader):
    data = {k: v for k, v in VALID_CONTRACT.items() if k != "description"}
    with pytest.raises(ContractValidationError):
        loader.load_dict(data)


def test_invalid_sprint_id_pattern(loader):
    """Scenario 2: sprint_id with spaces fails pattern."""
    data = {**VALID_CONTRACT, "sprint_id": "bad id with spaces"}
    with pytest.raises(ContractValidationError) as exc_info:
        loader.load_dict(data)
    assert "sprint_id" in str(exc_info.value)


def test_invalid_sprint_id_empty(loader):
    data = {**VALID_CONTRACT, "sprint_id": ""}
    with pytest.raises(ContractValidationError):
        loader.load_dict(data)


def test_invalid_permission_mode(loader):
    data = json.loads(json.dumps(VALID_CONTRACT))
    data["phases"]["implement"]["permission_mode"] = "invalid_mode"
    with pytest.raises(ContractValidationError) as exc_info:
        loader.load_dict(data)
    assert "permission_mode" in str(exc_info.value)


def test_invalid_max_retries_range(loader):
    data = {**VALID_CONTRACT, "max_retries": 99}
    with pytest.raises(ContractValidationError):
        loader.load_dict(data)


def test_invalid_max_retries_negative(loader):
    data = {**VALID_CONTRACT, "max_retries": -1}
    with pytest.raises(ContractValidationError):
        loader.load_dict(data)


def test_invalid_budget_zero(loader):
    data = {**VALID_CONTRACT, "max_sprint_budget_usd": 0}
    with pytest.raises(ContractValidationError):
        loader.load_dict(data)


def test_invalid_budget_negative(loader):
    data = {**VALID_CONTRACT, "max_sprint_budget_usd": -5.0}
    with pytest.raises(ContractValidationError):
        loader.load_dict(data)


def test_invalid_timeout_too_low(loader):
    data = json.loads(json.dumps(VALID_CONTRACT))
    data["phases"]["implement"]["timeout_s"] = 5
    with pytest.raises(ContractValidationError):
        loader.load_dict(data)


def test_invalid_timeout_too_high(loader):
    data = json.loads(json.dumps(VALID_CONTRACT))
    data["phases"]["implement"]["timeout_s"] = 9999
    with pytest.raises(ContractValidationError):
        loader.load_dict(data)


def test_invalid_gated_action(loader):
    data = {**VALID_CONTRACT, "gated_actions": ["not_a_real_action"]}
    with pytest.raises(ContractValidationError) as exc_info:
        loader.load_dict(data)
    assert "not_a_real_action" in str(exc_info.value)


def test_empty_phases(loader):
    data = {**VALID_CONTRACT, "phases": {}}
    with pytest.raises(ContractValidationError):
        loader.load_dict(data)


def test_valid_gated_actions(loader):
    data = {**VALID_CONTRACT, "gated_actions": ["commit", "push"]}
    contract = loader.load_dict(data)
    assert contract.gated_actions == ["commit", "push"]


def test_phase_missing_model(loader):
    data = json.loads(json.dumps(VALID_CONTRACT))
    del data["phases"]["implement"]["model"]
    with pytest.raises(ContractValidationError):
        loader.load_dict(data)


def test_validate_returns_empty_for_valid(loader):
    errors = loader.validate(VALID_CONTRACT)
    assert errors == []


def test_validate_returns_errors_list(loader):
    data = {**VALID_CONTRACT, "sprint_id": "bad id"}
    errors = loader.validate(data)
    assert len(errors) > 0
    assert any("sprint_id" in e for e in errors)


def test_file_not_found(loader, tmp_path):
    with pytest.raises(ContractValidationError):
        loader.load(tmp_path / "nonexistent.json")


def test_poc_contract_loads(loader):
    """The shipped PoC contract must be valid."""
    path = Path(__file__).parent.parent / "sprint_contracts" / "poc_fix_calculator.json"
    contract = loader.load(path)
    assert contract.sprint_id == "poc-001"
    assert "implement" in contract.phases
