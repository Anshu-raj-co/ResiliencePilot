"""Load safety limits and the experiment catalogue from config/."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARD_MAX_DURATION_S = 30  # non-negotiable ceiling; config may only lower it


@dataclass(frozen=True)
class SafetyConfig:
    compose_project: str
    rfms_base_url: str
    max_duration_s: int
    poll_interval_s: float
    http_timeout_s: float
    stale_threshold_s: float
    observer_loss_max_consecutive: int
    precheck_fresh_consecutive: int
    recovery_timeout_s: float
    recovery_consecutive: int
    watchdog_grace_s: float
    data_dir: Path

    def __post_init__(self) -> None:
        if not 1 <= self.max_duration_s <= HARD_MAX_DURATION_S:
            raise ValueError(f"max_duration_s must be within 1..{HARD_MAX_DURATION_S}")
        for name in ("poll_interval_s", "http_timeout_s", "stale_threshold_s", "recovery_timeout_s", "watchdog_grace_s"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        if min(self.observer_loss_max_consecutive, self.recovery_consecutive, self.precheck_fresh_consecutive) < 1:
            raise ValueError("consecutive counters must be >= 1")


@dataclass(frozen=True)
class ExperimentSpec:
    name: str
    mechanism: str
    allowed_targets: tuple[str, ...]
    max_duration_s: int
    description: str = ""
    expected_symptoms: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)


def load_safety_config(root: Path = REPO_ROOT) -> SafetyConfig:
    raw = json.loads((root / "config" / "safety.json").read_text(encoding="utf-8"))
    data_dir = Path(raw.pop("data_dir"))
    return SafetyConfig(data_dir=data_dir if data_dir.is_absolute() else root / data_dir, **raw)


def load_experiment_catalog(root: Path = REPO_ROOT) -> dict[str, ExperimentSpec]:
    raw = json.loads((root / "config" / "experiments.json").read_text(encoding="utf-8"))
    return {
        name: ExperimentSpec(
            name=name,
            mechanism=spec["mechanism"],
            allowed_targets=tuple(spec["allowed_targets"]),
            max_duration_s=min(int(spec["max_duration_s"]), HARD_MAX_DURATION_S),
            description=spec.get("description", ""),
            expected_symptoms=tuple(spec.get("expected_symptoms", [])),
            risks=tuple(spec.get("risks", [])),
        )
        for name, spec in raw.items()
    }
