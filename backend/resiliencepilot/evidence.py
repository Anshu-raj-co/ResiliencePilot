"""Evidence store: JSON files under data_dir. Writes are atomic (tmp + replace)."""
from __future__ import annotations

import json
import os
from pathlib import Path


class EvidenceStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        (data_dir / "runs").mkdir(parents=True, exist_ok=True)
        (data_dir / "baselines").mkdir(parents=True, exist_ok=True)

    def _write(self, path: Path, payload: dict) -> Path:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def save_run(self, run: dict) -> Path:
        return self._write(self.data_dir / "runs" / f"{run['run_id']}.json", run)

    def load_run(self, run_id: str) -> dict | None:
        if not run_id.replace("-", "").isalnum():
            return None
        p = self.data_dir / "runs" / f"{run_id}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def list_runs(self) -> list[str]:
        return sorted(p.stem for p in (self.data_dir / "runs").glob("*.json"))

    def save_baseline(self, name: str, payload: dict) -> Path:
        return self._write(self.data_dir / "baselines" / f"{name}.json", payload)
