"""Fault adapters and the watchdog. Only the orchestrator (never the AI or UI) constructs these."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from .docker_cli import Docker, DockerError


@dataclass
class RollbackResult:
    attempted: bool
    verified: bool
    by: str | None
    detail: str | None


class PauseFault:
    """telemetry_stall: freeze a container with the cgroup freezer (`docker pause`); roll back with `docker unpause`.

    rollback() is idempotent, serialised, and always re-inspects the container, so the watchdog and the
    orchestrator can both call it safely and a late-landing pause is still undone.
    """

    mechanism = "docker_pause"

    def __init__(self, docker: Docker, target: str, retries: int = 3, retry_delay_s: float = 0.2) -> None:
        self.docker = docker
        self.target = target
        self.retries = retries
        self.retry_delay_s = retry_delay_s
        self._lock = threading.Lock()
        self.inject_attempted = False
        self.result: RollbackResult | None = None

    def inject(self) -> None:
        self.inject_attempted = True
        self.docker.pause(self.target)

    def verify_injected(self) -> bool:
        st = self.docker.inspect(self.target)
        return st.exists and st.paused

    def rollback(self, by: str) -> RollbackResult:
        with self._lock:
            # deliberately no cached shortcut: a pause that lands late must be seen and undone
            last_err = None
            for _ in range(self.retries):
                try:
                    st = self.docker.inspect(self.target)
                    if st.exists and st.running and not st.paused:
                        if self.result is not None and self.result.verified:  # keep who actually rolled back
                            self.result = RollbackResult(True, True, self.result.by, f"re-verified by {by}")
                        else:
                            self.result = RollbackResult(True, True, by, last_err or "target running and unpaused")
                        return self.result
                    if st.paused:
                        self.docker.unpause(self.target)
                except DockerError as exc:
                    last_err = str(exc)
                time.sleep(self.retry_delay_s)
            try:
                st = self.docker.inspect(self.target)
                ok = st.exists and st.running and not st.paused
                detail = "verified after retries" if ok else f"final status={st.status}; last error: {last_err}"
            except DockerError as exc:
                ok, detail = False, f"final inspect failed: {exc}; last error: {last_err}"
            self.result = RollbackResult(True, ok, by, detail)
            return self.result


class Watchdog(threading.Thread):
    """Fires once at an absolute monotonic deadline unless cancelled. Independent of API, UI and AI."""

    def __init__(self, deadline_mono: float, on_fire: Callable[[], None], tick_s: float = 0.05) -> None:
        super().__init__(name="rp-watchdog", daemon=True)
        self.deadline_mono = deadline_mono
        self.on_fire = on_fire
        self.tick_s = tick_s
        self._cancelled = threading.Event()
        self.fired = threading.Event()

    def run(self) -> None:
        while not self._cancelled.wait(self.tick_s):
            if time.monotonic() >= self.deadline_mono:
                self.fired.set()
                self.on_fire()
                return

    def cancel(self) -> None:
        self._cancelled.set()
