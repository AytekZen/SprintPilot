"""Sprint contract loading and validation."""
from __future__ import annotations
import json
import re
from pathlib import Path

from .models import SprintContract, PhaseConfig

KNOWN_GATED_ACTIONS = {
    "commit", "push", "merge", "deploy", "db_write",
    "rm_recursive", "cloud", "paid_api", "infra", "credential_change",
}

VALID_PERMISSION_MODES = {"acceptEdits", "auto", "dontAsk", "manual", "plan"}
VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


class ContractValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Contract validation failed:\n" + "\n".join(f"  - {e}" for e in errors))


class ContractLoader:
    def load(self, path: Path) -> SprintContract:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractValidationError([f"Cannot read contract: {exc}"]) from exc
        errors = self.validate(data)
        if errors:
            raise ContractValidationError(errors)
        return self._build(data)

    def load_dict(self, data: dict) -> SprintContract:
        errors = self.validate(data)
        if errors:
            raise ContractValidationError(errors)
        return self._build(data)

    def validate(self, data: dict) -> list[str]:
        errors: list[str] = []
        if not isinstance(data, dict):
            return ["Contract must be a JSON object"]

        required = (
            "sprint_id", "description", "allowed_paths", "gated_actions",
            "max_retries", "max_sprint_budget_usd", "phases",
        )
        for f in required:
            if f not in data:
                errors.append(f"Missing required field: {f!r}")

        if errors:
            return errors

        sid = data["sprint_id"]
        if not isinstance(sid, str) or not sid:
            errors.append("sprint_id must be a non-empty string")
        elif not re.match(r'^[a-zA-Z0-9_-]+$', sid):
            errors.append("sprint_id must match [a-zA-Z0-9_-]+")

        if not isinstance(data["description"], str) or not data["description"].strip():
            errors.append("description must be a non-empty string")

        if not isinstance(data["allowed_paths"], list):
            errors.append("allowed_paths must be a list")
        else:
            for i, p in enumerate(data["allowed_paths"]):
                if not isinstance(p, str):
                    errors.append(f"allowed_paths[{i}] must be a string")

        if not isinstance(data["gated_actions"], list):
            errors.append("gated_actions must be a list")
        else:
            for ga in data["gated_actions"]:
                if ga not in KNOWN_GATED_ACTIONS:
                    errors.append(
                        f"Unknown gated action: {ga!r}. Valid: {sorted(KNOWN_GATED_ACTIONS)}"
                    )

        mr = data["max_retries"]
        if not isinstance(mr, int) or isinstance(mr, bool):
            errors.append("max_retries must be an integer")
        elif not (0 <= mr <= 10):
            errors.append("max_retries must be between 0 and 10")

        mb = data["max_sprint_budget_usd"]
        if not isinstance(mb, (int, float)) or isinstance(mb, bool):
            errors.append("max_sprint_budget_usd must be a number")
        elif mb <= 0:
            errors.append("max_sprint_budget_usd must be > 0")

        phases = data["phases"]
        if not isinstance(phases, dict) or not phases:
            errors.append("phases must be a non-empty object")
        else:
            for phase_name, phase_data in phases.items():
                errors.extend(self._validate_phase(phase_name, phase_data))

        return errors

    def _validate_phase(self, name: str, data: dict) -> list[str]:
        errors = []
        prefix = f"phases.{name}"
        if not isinstance(data, dict):
            return [f"{prefix}: must be an object"]
        for f in ("allowed_tools", "permission_mode", "budget_usd", "timeout_s", "model"):
            if f not in data:
                errors.append(f"{prefix}: missing required field {f!r}")
        if errors:
            return errors
        if not isinstance(data["allowed_tools"], list):
            errors.append(f"{prefix}.allowed_tools: must be a list")
        pm = data["permission_mode"]
        if pm not in VALID_PERMISSION_MODES:
            errors.append(
                f"{prefix}.permission_mode: {pm!r} invalid. Valid: {sorted(VALID_PERMISSION_MODES)}"
            )
        bd = data["budget_usd"]
        if not isinstance(bd, (int, float)) or isinstance(bd, bool) or bd <= 0:
            errors.append(f"{prefix}.budget_usd: must be a positive number")
        ts = data["timeout_s"]
        if not isinstance(ts, int) or isinstance(ts, bool) or not (10 <= ts <= 3600):
            errors.append(f"{prefix}.timeout_s: must be an integer between 10 and 3600")
        if not isinstance(data["model"], str) or not data["model"]:
            errors.append(f"{prefix}.model: must be a non-empty string")
        effort = data.get("effort", "medium")
        if effort not in VALID_EFFORTS:
            errors.append(f"{prefix}.effort: {effort!r} invalid. Valid: {sorted(VALID_EFFORTS)}")
        return errors

    def _build(self, data: dict) -> SprintContract:
        phases = {
            name: PhaseConfig.from_dict(name, pd)
            for name, pd in data["phases"].items()
        }
        return SprintContract(
            sprint_id=data["sprint_id"],
            description=data["description"],
            allowed_paths=data["allowed_paths"],
            gated_actions=data["gated_actions"],
            max_retries=data["max_retries"],
            max_sprint_budget_usd=data["max_sprint_budget_usd"],
            phases=phases,
        )
