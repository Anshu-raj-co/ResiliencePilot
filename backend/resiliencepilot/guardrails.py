"""Automatic abort conditions during the fault window: error rate, latency p95 and blast radius.

The guards only *detect*; the orchestrator then stops the fault window and its existing `finally` path rolls back.
They never touch Docker themselves (the blast-radius monitor only reads).
"""
from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass

from .docker_cli import Docker, DockerError, SnapshotEntry


def p95(values: list[float]) -> float | None:
    """Nearest-rank p95 (no interpolation): the smallest value >= 95 % of the samples."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


@dataclass
class Violation:
    guard: str          # "error_rate" | "latency_p95" | "blast_radius"
    detail: dict


class ErrorRateGuard:
    """Share of fault-window polls with an HTTP response other than 200. Polls without a response are not errors
    here (the observer-loss rule handles them). Codes in `expected` are the experiment's declared symptom (e.g. 500
    while InfluxDB is down): counted separately, never as errors. Anything else still counts."""

    def __init__(self, max_error_rate: float, min_samples: int = 5, expected: tuple[int, ...] = ()) -> None:
        self.max_error_rate = max_error_rate
        self.min_samples = min_samples
        self.expected = frozenset(expected)
        self.responses = 0
        self.errors = 0
        self.expected_seen = 0

    def update(self, http_status: int | None) -> Violation | None:
        if http_status is None:
            return None
        if http_status in self.expected:
            self.expected_seen += 1
            return None
        self.responses += 1
        if http_status != 200:
            self.errors += 1
        rate = self.errors / self.responses
        judged = self.max_error_rate == 0.0 or self.responses >= self.min_samples
        if self.errors >= 1 and judged and rate > self.max_error_rate:
            return Violation("error_rate", {"errors": self.errors, "responses": self.responses,
                                            "error_rate": round(rate, 3), "max_error_rate": self.max_error_rate,
                                            "last_status": http_status})
        return None


class LatencyGuard:
    """Rolling p95 of the most recent fault-window latencies against a threshold derived from the baseline."""

    def __init__(self, threshold_ms: float, window: int, min_samples: int) -> None:
        self.threshold_ms = threshold_ms
        self.samples: deque[float] = deque(maxlen=window)
        self.min_samples = min_samples

    def update(self, latency_ms: float | None) -> Violation | None:
        if latency_ms is None:
            return None
        self.samples.append(latency_ms)
        if len(self.samples) < self.min_samples:
            return None
        cur = p95(list(self.samples))
        if cur is not None and cur > self.threshold_ms:
            return Violation("latency_p95", {"p95_ms": round(cur, 2), "threshold_ms": round(self.threshold_ms, 2),
                                             "samples": len(self.samples)})
        return None


def latency_threshold(baseline_p95_ms: float | None, multiplier: float, floor_ms: float) -> float:
    if baseline_p95_ms is None:
        return floor_ms
    return max(baseline_p95_ms * multiplier, floor_ms)


def blast_radius_changes(baseline: dict[str, SnapshotEntry], current: dict[str, SnapshotEntry],
                         allowed: set[str]) -> list[dict]:
    """Every container outside `allowed` that appeared, disappeared, or changed id/status/start time."""
    changes = []
    for name in sorted(set(baseline) | set(current)):
        if name in allowed:
            continue
        before, after = baseline.get(name), current.get(name)
        if before == after:
            continue
        changes.append({"container": name,
                        "before": None if before is None else {"status": before.status, "started_at": before.started_at, "id": before.id[:12]},
                        "after": None if after is None else {"status": after.status, "started_at": after.started_at, "id": after.id[:12]}})
    return changes


class BlastRadiusMonitor(threading.Thread):
    """Read-only monitor thread. Sets `violation` and `tripped` on the first change outside the blast radius, or when
    the project cannot be observed `max_failures` times in a row (loss of reliable observation)."""

    def __init__(self, docker: Docker, project: str, ids: list[str], baseline: dict[str, SnapshotEntry],
                 allowed: set[str], interval_s: float, max_failures: int, relist_every: int = 5) -> None:
        super().__init__(name="rp-blast-radius", daemon=True)
        self.docker, self.project = docker, project
        self.ids, self.baseline, self.allowed = list(ids), baseline, set(allowed)
        self.interval_s, self.max_failures, self.relist_every = interval_s, max_failures, relist_every
        self._stop = threading.Event()
        self.tripped = threading.Event()
        self.violation: Violation | None = None
        self.checks = 0

    def check_once(self) -> Violation | None:
        if self.checks % self.relist_every == 0:  # catch containers added to / removed from the project
            self.ids = sorted(set(self.ids) | set(self.docker.project_container_ids(self.project)))
        self.checks += 1
        changes = blast_radius_changes(self.baseline, self.docker.snapshot(self.ids), self.allowed)
        if changes:
            return Violation("blast_radius", {"changes": changes, "allowed": sorted(self.allowed)})
        return None

    def run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                v = self.check_once()
                failures = 0
            except DockerError as exc:
                failures += 1
                v = None
                if failures >= self.max_failures:
                    v = Violation("blast_radius", {"unobservable": True, "consecutive_failures": failures,
                                                   "error": str(exc)})
            if v is not None:
                self.violation = v
                self.tripped.set()
                return
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        self._stop.set()
