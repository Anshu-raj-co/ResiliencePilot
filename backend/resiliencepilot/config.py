"""Load safety limits and the experiment catalogue from config/."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARD_MAX_DURATION_S = 30  # non-negotiable ceiling; config may only lower it
MAX_WATCHDOG_GRACE_S = 10.0
MAX_ERROR_RATE = 0.5  # a tolerated error rate above 50 % would let a broken service pass as an experiment symptom


SUPPORTED_MECHANISMS = ("docker_pause", "docker_stop")


@dataclass(frozen=True)
class Guardrails:
    """Automatic abort-and-rollback conditions evaluated during the fault window.

    error rate   : share of fault-window polls answered with a non-200 status (no-response polls are counted by
                   observer-loss instead). max_error_rate 0.0: the first error aborts. Above 0: the rate is judged
                   once `error_min_samples` responses exist (a single early error is not a 100 % rate).
    latency p95  : rolling p95 of the last `latency_window` fault-window polls. Abort when it exceeds
                   max(baseline_p95 * latency_p95_multiplier, latency_p95_floor_ms), once `latency_min_samples` exist.
    blast radius : every compose-project container outside the experiment's declared blast_radius must keep the same
                   id, status and start time. Checked by a monitor thread every `blast_radius_interval_s`.
    """
    max_error_rate: float = 0.0
    error_min_samples: int = 5
    latency_p95_multiplier: float = 5.0
    latency_p95_floor_ms: float = 250.0
    latency_window: int = 10
    latency_min_samples: int = 5
    blast_radius_interval_s: float = 0.5
    blast_radius_max_snapshot_failures: int = 3

    def __post_init__(self) -> None:
        # lower AND upper bounds: config may tighten a guard, never switch it off inside a 30 s window
        if not 0.0 <= self.max_error_rate <= MAX_ERROR_RATE:
            raise ValueError(f"max_error_rate must be within [0, {MAX_ERROR_RATE}]")
        if not 1 <= self.error_min_samples <= 10:
            raise ValueError("error_min_samples must be within 1..10")
        if not 1.0 <= self.latency_p95_multiplier <= 20.0 or not 0 < self.latency_p95_floor_ms <= 5000:
            raise ValueError("latency guard: multiplier within 1..20, floor within (0, 5000] ms")
        if not 1 <= self.latency_min_samples <= self.latency_window <= 30:
            raise ValueError("need 1 <= latency_min_samples <= latency_window <= 30")
        if not 0 < self.blast_radius_interval_s <= 2.0 or not 1 <= self.blast_radius_max_snapshot_failures <= 5:
            raise ValueError("blast-radius monitor: interval within (0, 2] s, max_snapshot_failures within 1..5")


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
    guardrails: Guardrails = field(default_factory=Guardrails)

    def __post_init__(self) -> None:
        if not 1 <= self.max_duration_s <= HARD_MAX_DURATION_S:
            raise ValueError(f"max_duration_s must be within 1..{HARD_MAX_DURATION_S}")
        for name in ("poll_interval_s", "http_timeout_s", "stale_threshold_s", "recovery_timeout_s", "watchdog_grace_s"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.watchdog_grace_s > MAX_WATCHDOG_GRACE_S:
            raise ValueError(f"watchdog_grace_s must be <= {MAX_WATCHDOG_GRACE_S}")
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
    blast_radius: tuple[str, ...] = field(default_factory=tuple)  # containers allowed to change state; default = targets
    stop_timeout_s: int = 2                                        # docker_stop only: SIGTERM -> SIGKILL grace
    max_error_rate: float | None = None                            # per-experiment override of the guardrail

    def __post_init__(self) -> None:
        if self.mechanism not in SUPPORTED_MECHANISMS:
            raise ValueError(f"{self.name}: unsupported mechanism {self.mechanism!r}")
        if not self.allowed_targets:
            raise ValueError(f"{self.name}: allowed_targets must not be empty")
        if not set(self.allowed_targets) <= set(self.effective_blast_radius):
            raise ValueError(f"{self.name}: blast_radius must include every allowed target")
        if not 0 <= self.stop_timeout_s <= 5:
            raise ValueError(f"{self.name}: stop_timeout_s must be within 0..5")
        if self.max_error_rate is not None and not 0.0 <= self.max_error_rate <= MAX_ERROR_RATE:
            raise ValueError(f"{self.name}: max_error_rate must be within [0, {MAX_ERROR_RATE}]")

    @property
    def effective_blast_radius(self) -> tuple[str, ...]:
        return self.blast_radius or self.allowed_targets


def load_safety_config(root: Path = REPO_ROOT) -> SafetyConfig:
    raw = json.loads((root / "config" / "safety.json").read_text(encoding="utf-8"))
    data_dir = Path(raw.pop("data_dir"))
    guardrails = Guardrails(**raw.pop("guardrails", {}))
    return SafetyConfig(data_dir=data_dir if data_dir.is_absolute() else root / data_dir, guardrails=guardrails, **raw)


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
            blast_radius=tuple(spec.get("blast_radius", [])),
            stop_timeout_s=int(spec.get("stop_timeout_s", 2)),
            max_error_rate=spec.get("max_error_rate"),
        )
        for name, spec in raw.items()
    }
