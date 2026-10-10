"""Test doubles that model the real blueprint: a forwarder that stamps createdDateTime unless paused."""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from resiliencepilot.config import SafetyConfig
from resiliencepilot.docker_cli import ContainerState, DockerError
from resiliencepilot.observation import Observation

T0 = datetime(2026, 10, 10, 8, 0, 0, tzinfo=timezone.utc)


def obs(t: float, created: float | None, status: int | None = 200, server: float | None = None) -> Observation:
    """Observation at local time T0+t with createdDateTime T0+created and server time T0+server (default t)."""
    ts = lambda x: None if x is None else T0 + timedelta(seconds=x)  # noqa: E731
    return Observation(
        observed_at=ts(t), http_status=status, latency_ms=5.0 if status else None,
        vin="VIN1" if created is not None else None, created_at=ts(created), received_at=ts(t),
        server_time=ts(server if server is not None else (t if status == 200 else None)),
    )


def make_config(tmp_path: Path, **overrides) -> SafetyConfig:
    base = SafetyConfig(
        compose_project="resiliencepilot-sdv", rfms_base_url="http://test", max_duration_s=30,
        poll_interval_s=0.05, http_timeout_s=0.5, stale_threshold_s=0.3, observer_loss_max_consecutive=3,
        precheck_fresh_consecutive=2, recovery_timeout_s=1.5, recovery_consecutive=3, watchdog_grace_s=0.5,
        data_dir=tmp_path / "data",
    )
    return replace(base, **overrides)


class FakeWorld:
    """Shared state between FakeDocker and FakeObserver: telemetry advances only while the forwarder runs."""

    def __init__(self, tick_s: float = 0.05) -> None:
        self.lock = threading.Lock()
        self.tick_s = tick_s
        self.containers = {"fms-forwarder": ContainerState("fms-forwarder", True, True, False, "running", "resiliencepilot-sdv")}
        self.last_created = datetime.now(timezone.utc)
        self.paused_since: float | None = None
        # failure knobs
        self.api_down = False
        self.stay_stale_after_unpause = False
        self.unpause_fails = False
        self.pause_is_noop = False
        self.pause_delay_s = 0.0
        self.block_observe_s = 0.0
        self.calls: list[tuple[float, str, str]] = []

    def forwarder_paused(self) -> bool:
        return self.containers["fms-forwarder"].paused


class FakeDocker:
    def __init__(self, world: FakeWorld) -> None:
        self.w = world

    def inspect(self, name: str) -> ContainerState:
        with self.w.lock:
            return self.w.containers.get(name, ContainerState(name, False))

    def pause(self, name: str) -> None:
        if self.w.pause_delay_s:
            time.sleep(self.w.pause_delay_s)  # docker pause that lands late
        with self.w.lock:
            self.w.calls.append((time.monotonic(), "pause", name))
            if not self.w.pause_is_noop:
                self.w.containers[name] = replace(self.w.containers[name], paused=True, status="paused")

    def unpause(self, name: str) -> None:
        with self.w.lock:
            self.w.calls.append((time.monotonic(), "unpause", name))
            if self.w.unpause_fails:
                raise DockerError("simulated unpause failure")
            self.w.containers[name] = replace(self.w.containers[name], paused=False, status="running")


class FakeObserver:
    def __init__(self, world: FakeWorld) -> None:
        self.w = world
        self.block_once_when_paused = False

    def observe(self) -> Observation:
        w = self.w
        if self.block_once_when_paused and w.forwarder_paused():
            self.block_once_when_paused = False
            time.sleep(w.block_observe_s)
        now = datetime.now(timezone.utc)
        with w.lock:
            if w.api_down:
                return Observation(now, None, None, None, None, None, None, error="ConnectError")
            frozen = w.forwarder_paused() or w.stay_stale_after_unpause and any(c[1] == "unpause" for c in w.calls)
            if not frozen:
                w.last_created = now - timedelta(seconds=0.01)
            created = w.last_created
        return Observation(now, 200, 3.0, "VIN1", created, now, now)
