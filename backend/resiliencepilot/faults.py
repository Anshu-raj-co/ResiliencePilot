"""Fault adapters and the watchdog. Only the orchestrator (never the AI or UI) constructs these."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from .config import ExperimentSpec
from .docker_cli import ContainerState, Docker, DockerError


@dataclass
class RollbackResult:
    attempted: bool
    verified: bool
    by: str | None
    detail: str | None


class _CannotRestore(Exception):
    """The container is in a state rollback must not touch (e.g. it was replaced by a different container)."""


class InjectAborted(Exception):
    """inject() refused because a rollback has already started (e.g. the watchdog fired while injecting)."""


class ContainerFault:
    """Shared rollback loop: idempotent, serialised and always re-inspecting, so the watchdog and the orchestrator
    can both call rollback() safely and a late-landing fault action is still undone."""

    mechanism = ""
    verified_by = ""          # how injection is verified (recorded in the timeline)
    restored_description = ""  # what "rolled back" means for this mechanism

    def __init__(self, docker: Docker, target: str, retries: int = 3, retry_delay_s: float = 0.2) -> None:
        self.docker = docker
        self.target = target
        self.retries = retries
        self.retry_delay_s = retry_delay_s
        self._lock = threading.Lock()
        self.inject_attempted = False
        self.rollback_started = False
        self.result: RollbackResult | None = None

    def bind_precheck(self, st: ContainerState) -> None:
        """Called with the target's state verified at precheck (before injection)."""

    def inject(self) -> None:
        """Inject under the same lock as rollback(): once a rollback has started, injection is refused, so a
        watchdog that fires mid-inject can never be followed by a late fault action."""
        with self._lock:
            if self.rollback_started:
                raise InjectAborted("rollback already started; not injecting")
            self.inject_attempted = True
            self._inject()

    # -- per mechanism --
    def _inject(self) -> None:
        raise NotImplementedError

    def verify_injected(self) -> bool:
        raise NotImplementedError

    def _is_restored(self, st: ContainerState) -> bool:
        raise NotImplementedError

    def _restore_step(self, st: ContainerState) -> None:
        raise NotImplementedError

    # -- shared --
    def rollback(self, by: str) -> RollbackResult:
        with self._lock:
            self.rollback_started = True
            last_err = None
            acted = False
            for _ in range(self.retries):
                try:
                    st = self.docker.inspect(self.target)
                    if self._is_restored(st):
                        prev = self.result
                        if prev is not None and prev.verified and not acted:  # keep who actually rolled back
                            self.result = RollbackResult(True, True, prev.by, f"re-verified by {by}")
                        elif prev is not None and prev.verified and acted:    # an earlier "verified" was wrong
                            self.result = RollbackResult(True, True, by, f"{self.restored_description}; "
                                                         f"earlier verification by {prev.by} superseded")
                        else:
                            self.result = RollbackResult(True, True, by, last_err or self.restored_description)
                        return self.result
                    acted = True
                    self._restore_step(st)
                except _CannotRestore as exc:
                    self.result = RollbackResult(True, False, by, str(exc))
                    return self.result
                except DockerError as exc:
                    last_err = str(exc)
                time.sleep(self.retry_delay_s)
            try:
                st = self.docker.inspect(self.target)
                ok = self._is_restored(st)
                detail = "verified after retries" if ok else f"final status={st.status}; last error: {last_err}"
            except DockerError as exc:
                ok, detail = False, f"final inspect failed: {exc}; last error: {last_err}"
            self.result = RollbackResult(True, ok, by, detail)
            return self.result


class PauseFault(ContainerFault):
    """telemetry_stall: freeze a container with the cgroup freezer (`docker pause`); roll back with `docker unpause`."""

    mechanism = "docker_pause"
    verified_by = "docker inspect State.Paused == true"
    restored_description = "target running and unpaused"

    def _inject(self) -> None:
        self.docker.pause(self.target)

    def verify_injected(self) -> bool:
        st = self.docker.inspect(self.target)
        return st.exists and st.paused

    def _is_restored(self, st: ContainerState) -> bool:
        return st.exists and st.running and not st.paused

    def _restore_step(self, st: ContainerState) -> None:
        if st.paused:
            self.docker.unpause(self.target)


class StopStartFault(ContainerFault):
    """service_crash: `docker stop -t N` (SIGTERM, SIGKILL after N s); roll back with `docker start`.

    Rollback is only verified for the SAME container (id recorded before the stop). A container that was removed or
    replaced is never "restored" by starting something else: that is reported as not verified.
    """

    mechanism = "docker_stop"
    verified_by = "docker inspect State.Running == false"
    restored_description = "same container (id unchanged) running and unpaused"

    def __init__(self, docker: Docker, target: str, stop_timeout_s: int = 2, **kw) -> None:
        super().__init__(docker, target, **kw)
        self.stop_timeout_s = stop_timeout_s
        self.expected_id: str | None = None
        self.exit_code: int | None = None

    def bind_precheck(self, st: ContainerState) -> None:
        self.expected_id = st.id

    def _inject(self) -> None:
        before = self.docker.inspect(self.target)
        if not before.exists or not before.id:
            raise DockerError(f"cannot record container id of {self.target} before stopping it")
        if self.expected_id is None:
            self.expected_id = before.id
        elif before.id != self.expected_id:
            raise DockerError(f"{self.target} changed since precheck (id {before.id[:12]} != {self.expected_id[:12]})")
        self.docker.stop(self.target, self.stop_timeout_s)

    def verify_injected(self) -> bool:
        st = self.docker.inspect(self.target)
        self.exit_code = st.exit_code  # 137 = killed after the stop grace period; 0/143 = exited on SIGTERM
        return st.exists and not st.running and st.id == self.expected_id

    def _is_restored(self, st: ContainerState) -> bool:
        same = self.expected_id is None or st.id == self.expected_id
        return st.exists and same and st.running and not st.paused

    def _restore_step(self, st: ContainerState) -> None:
        if not st.exists:
            raise _CannotRestore(f"{self.target} no longer exists; not recreating it")
        if self.expected_id is None and not st.running:
            raise _CannotRestore(f"{self.target} is stopped but its id was never recorded; not starting it")
        if self.expected_id is not None and st.id != self.expected_id:
            raise _CannotRestore(f"{self.target} was replaced (id {st.id and st.id[:12]} != {self.expected_id[:12]})")
        if st.paused:
            self.docker.unpause(self.target)
        elif not st.running:
            self.docker.start(self.target)


def make_fault(spec: ExperimentSpec, docker: Docker, target: str) -> ContainerFault:
    if spec.mechanism == "docker_pause":
        return PauseFault(docker, target)
    if spec.mechanism == "docker_stop":
        return StopStartFault(docker, target, stop_timeout_s=spec.stop_timeout_s)
    raise ValueError(f"no fault adapter for mechanism {spec.mechanism!r}")


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
