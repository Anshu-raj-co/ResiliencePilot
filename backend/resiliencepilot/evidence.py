"""Evidence store: JSON files under data_dir. Writes are atomic (tmp + replace)."""
from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path


def host_platform() -> dict:
    """OS and CPU of the machine running ResiliencePilot, stored with each run so statistics can be compared fairly.
    The Docker VM may differ (e.g. linux/amd64 images emulated on Apple Silicon, D-023)."""
    system = platform.system()
    release = platform.mac_ver()[0] if system == "Darwin" else platform.release()
    return {"os": {"Darwin": "macOS"}.get(system, system), "os_release": release, "cpu": platform.machine()}


def platform_label(p: dict | None) -> str:
    if not p:
        return "not recorded"
    return " ".join(x for x in (p.get("os"), p.get("os_release")) if x) + (f" ({p['cpu']})" if p.get("cpu") else "")


class EvidenceStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        (data_dir / "runs").mkdir(parents=True, exist_ok=True)
        (data_dir / "baselines").mkdir(parents=True, exist_ok=True)

    def _write(self, path: Path, payload: dict) -> Path:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        # On Windows, os.replace fails with "Access is denied" while a reader (e.g. the dashboard API) has the
        # target open for a moment. Retry briefly instead of losing evidence.
        for attempt in range(40):
            try:
                os.replace(tmp, path)
                return path
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.025)
        return path

    def save_run(self, run: dict) -> Path:
        return self._write(self.data_dir / "runs" / f"{run['run_id']}.json", run)

    def load_run(self, run_id: str) -> dict | None:
        if not run_id.replace("-", "").isalnum():
            return None
        p = self.data_dir / "runs" / f"{run_id}.json"
        for attempt in range(40):  # Windows: opening while a writer replaces the file -> PermissionError
            try:
                return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.025)
        return None

    def list_runs(self) -> list[str]:
        return sorted(p.stem for p in (self.data_dir / "runs").glob("*.json"))

    def latest_baseline(self) -> tuple[str, dict] | None:
        """(name, summary) of the newest stored baseline that recorded latency, or None."""
        for p in sorted((self.data_dir / "baselines").glob("*.json"), reverse=True):
            try:
                summary = json.loads(p.read_text(encoding="utf-8")).get("summary") or {}
            except (OSError, ValueError):
                continue
            if (summary.get("latency_ms") or {}).get("p95") is not None:
                return p.stem, summary
        return None

    def save_baseline(self, name: str, payload: dict) -> Path:
        return self._write(self.data_dir / "baselines" / f"{name}.json", payload)
