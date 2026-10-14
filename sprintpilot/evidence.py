"""Evidence store with atomic file writes."""
from __future__ import annotations
import json
import os
from pathlib import Path


class EvidenceStoreError(Exception):
    pass


class EvidenceStore:
    def __init__(self, base_dir: Path, sprint_id: str) -> None:
        self.sprint_dir = (base_dir / sprint_id).resolve()
        self.sprint_dir.mkdir(parents=True, exist_ok=True)

    def write(self, filename: str, data: dict | list) -> Path:
        """Atomically write JSON data."""
        dest = self.sprint_dir / filename
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            os.replace(str(tmp), str(dest))
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise EvidenceStoreError(f"Failed to write {filename}: {exc}") from exc
        return dest

    def write_text(self, filename: str, content: str) -> Path:
        """Atomically write text content."""
        dest = self.sprint_dir / filename
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(str(tmp), str(dest))
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise EvidenceStoreError(f"Failed to write {filename}: {exc}") from exc
        return dest

    def read(self, filename: str) -> dict | list | None:
        """Read JSON file, returns None if not found."""
        path = self.sprint_dir / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise EvidenceStoreError(f"Failed to read {filename}: {exc}") from exc

    def read_text(self, filename: str) -> str | None:
        path = self.sprint_dir / filename
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def exists(self, filename: str) -> bool:
        return (self.sprint_dir / filename).exists()

    def list_files(self) -> list[str]:
        return sorted(p.name for p in self.sprint_dir.iterdir() if p.is_file())
