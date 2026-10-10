"""Test doubles that model the real blueprint: a forwarder that stamps createdDateTime unless paused or stopped."""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from resiliencepilot.config import SafetyConfig
from resiliencepilot.docker_cli import ContainerState, DockerError, SnapshotEntry
from resiliencepilot.observation import Observation

T0 = datetime(2026, 10, 10, 8, 0, 0, tzinfo=timezone.utc)
PROJECT = "resiliencepilot-sdv"


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
        compose_project=PROJECT, rfms_base_url="http://test", max_duration_s=30,
        poll_interval_s=0.05, http_timeout_s=0.5, stale_threshold_s=0.3, observer_loss_max_consecutive=3,
        precheck_fresh_consecutive=2, recovery_timeout_s=1.5, recovery_consecutive=3, watchdog_grace_s=0.5,
        data_dir=tmp_path / "data",
    )
    return replace(base, **overrides)


def _container(name: str, n: int) -> ContainerState:
    return ContainerState(name, True, True, False, "running", PROJECT, id=f"{n:064x}", started_at="2026-10-10T08:00:00Z")


class FakeWorld:
    """Shared state between FakeDocker and FakeObserver: telemetry advances only while the forwarder runs."""

    def __init__(self, tick_s: float = 0.05) -> None:
        self.lock = threading.Lock()
        self.tick_s = tick_s
        self.containers = {"fms-forwarder": _container("fms-forwarder", 1), "influxDB": _container("influxDB", 2)}
        self.last_created = datetime.now(timezone.utc)
        self.paused_since: float | None = None
        self.starts = 0
        # failure knobs
        self.api_down = False
        self.api_status = 200            # status the rFMS API answers with (e.g. 500)
        self.latency_ms = 3.0            # latency the observer reports
        self.stay_stale_after_unpause = False
        self.unpause_fails = False
        self.start_fails = False
        self.stop_is_noop = False
        self.replace_on_stop = False     # the stopped container is swapped for a new one (different id)
        self.pause_is_noop = False
        self.pause_delay_s = 0.0
        self.block_observe_s = 0.0
        self.snapshot_fails = False
        self.influx_down_status: int | None = 500   # what the API answers while influxDB is stopped (None = no response)
        self.influx_down_delay_s = 0.0              # how long the API takes to answer while influxDB is stopped
        self.stored: list[datetime] = []            # statuses the backend actually stored (not produced while influx is down)
        self.calls: list[tuple[float, str, str]] = []

    def forwarder_paused(self) -> bool:
        return self.containers["fms-forwarder"].paused

    def forwarder_down(self) -> bool:
        c = self.containers["fms-forwarder"]
        return c.paused or not c.running

    def influx_down(self) -> bool:
        c = self.containers["influxDB"]
        return c.paused or not c.running

    def mutate(self, name: str, **changes) -> None:
        """Change a container out-of-band (e.g. a neighbour restarting) to exercise the blast-radius guard."""
        with self.lock:
            self.containers[name] = replace(self.containers[name], **changes)


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

    def stop(self, name: str, timeout_s: int) -> None:
        with self.w.lock:
            self.w.calls.append((time.monotonic(), "stop", name))
            if self.w.stop_is_noop:
                return
            c = replace(self.w.containers[name], running=False, status="exited")
            if self.w.replace_on_stop:
                c = replace(c, id=f"{999:064x}")
            self.w.containers[name] = c

    def start(self, name: str) -> None:
        with self.w.lock:
            self.w.calls.append((time.monotonic(), "start", name))
            if self.w.start_fails:
                raise DockerError("simulated start failure")
            self.w.starts += 1
            self.w.containers[name] = replace(self.w.containers[name], running=True, paused=False, status="running",
                                              started_at=f"2026-10-10T09:00:{self.w.starts:02d}Z")

    def project_container_ids(self, project: str) -> list[str]:
        with self.w.lock:
            if self.w.snapshot_fails:
                raise DockerError("simulated docker ps failure")
            return sorted(c.id for c in self.w.containers.values() if c.project == project)

    def snapshot(self, ids: list[str]) -> dict[str, SnapshotEntry]:
        with self.w.lock:
            if self.w.snapshot_fails:
                raise DockerError("simulated docker inspect failure")
            return {c.name: SnapshotEntry(c.id, c.status, c.started_at)
                    for c in self.w.containers.values() if c.id in ids}


class FakeObserver:
    def __init__(self, world: FakeWorld) -> None:
        self.w = world
        self.block_once_when_paused = False

    def observe(self, timeout_s: float | None = None) -> Observation:
        w = self.w
        timeout = 0.5 if timeout_s is None else timeout_s   # mirrors make_config(http_timeout_s=0.5)
        if w.influx_down() and w.influx_down_delay_s:
            time.sleep(min(w.influx_down_delay_s, timeout))
            if w.influx_down_delay_s > timeout:
                return Observation(datetime.now(timezone.utc), None, None, None, None, None, None, error="ReadTimeout")
        if self.block_once_when_paused and w.forwarder_down():
            self.block_once_when_paused = False
            time.sleep(w.block_observe_s)
        now = datetime.now(timezone.utc)
        with w.lock:
            if w.api_down:
                return Observation(now, None, None, None, None, None, None, error="ConnectError")
            if w.api_status != 200:
                return Observation(now, w.api_status, w.latency_ms, None, None, None, None, error=f"HTTP {w.api_status}")
            frozen = w.forwarder_down() or w.stay_stale_after_unpause and any(c[1] == "unpause" for c in w.calls)
            if w.influx_down():                       # store unreachable: produced statuses are dropped
                if w.influx_down_status is None:
                    return Observation(now, None, None, None, None, None, None, error="ReadTimeout")
                lat = w.influx_down_delay_s * 1000 if w.influx_down_delay_s else w.latency_ms
                return Observation(now, w.influx_down_status, lat, None, None, None, None,
                                   error=f"HTTP {w.influx_down_status}")
            if not frozen:
                w.last_created = now - timedelta(seconds=0.01)
                w.stored.append(w.last_created)
            created = w.last_created
        return Observation(now, 200, w.latency_ms, "VIN1", created, now, now)

    def statuses_between(self, start: datetime, end: datetime) -> list[datetime]:
        with self.w.lock:
            return sorted(c for c in self.w.stored if start <= c <= end)
