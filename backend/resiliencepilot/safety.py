"""Deterministic safety controller. Small on purpose; every rule here has a negative-path test.

Rules enforced:
  * strict request schema (unknown fields, wrong types, unknown experiment types rejected)
  * target must be allowlisted for the experiment type AND belong to the sandbox compose project
  * duration <= min(config max, catalogue max, 30 s)
  * exactly one active experiment ACROSS PROCESSES (exclusive lock file holding the owner PID)
  * latched-unsafe state persists on disk and blocks all experiments until an explicit, checked reset
  * an "active fault" marker is written before injection so an interrupted process can be cleaned up
  * emergency stop: in-process event, or the EMERGENCY_STOP file written by scripts/kill-switch.sh
  * unreadable state/marker files are treated as unsafe, never as "all clear"
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .config import HARD_MAX_DURATION_S, ExperimentSpec, SafetyConfig
from .docker_cli import ContainerState, Docker, DockerError
from .observation import utcnow


class SafetyError(Exception):
    """Base class: the controller refused an operation."""


class RequestRejected(SafetyError):
    pass


class Busy(SafetyError):
    pass


class Latched(SafetyError):
    pass


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    experiment_type: Literal["telemetry_stall", "service_crash", "dependency_failure"]
    target: str = Field(min_length=1, max_length=63, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    duration_s: int = Field(ge=1, le=HARD_MAX_DURATION_S)
    approved_by: str = Field(min_length=1, max_length=80)
    hypothesis: str | None = Field(default=None, max_length=500)

    @field_validator("approved_by")
    @classmethod
    def _approver_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("approved_by must name a human approver")
        return v.strip()


def pid_alive(pid: int) -> bool:
    """True if a process with this PID exists. Never sends a signal (os.kill on Windows would terminate it)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# On Windows, replacing/deleting/reading a file fails with PermissionError while another thread or process has it
# open (e.g. a status poller). Retry ONLY that error, briefly and boundedly; then fail exactly as before (closed).
# Never used for decisions, lock acquisition (O_EXCL) or anything with a timeout.
IO_RETRY_DELAYS_S = (0.02, 0.03, 0.04, 0.05)  # 5 attempts in total, 20-50 ms apart (<= 140 ms)


def _retry_io(op):
    for delay in IO_RETRY_DELAYS_S:
        try:
            return op()
        except PermissionError:
            time.sleep(delay)
    return op()  # 5th and last attempt: any error propagates unchanged


def _unlink(path: Path) -> None:
    _retry_io(lambda: path.unlink(missing_ok=True))


def _read_text(path: Path) -> str:
    return _retry_io(lambda: path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _retry_io(lambda: os.replace(tmp, path))


class SafetyController:
    def __init__(self, cfg: SafetyConfig, catalog: dict[str, ExperimentSpec], docker: Docker) -> None:
        self.cfg = cfg
        self.catalog = catalog
        self.docker = docker
        self._lock = threading.RLock()  # re-entrant: a signal handler or nested call can never self-deadlock
        self._active_run: str | None = None
        self._stop_event: threading.Event | None = None
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = cfg.data_dir / "safety_state.json"
        self._marker_path = cfg.data_dir / "active_fault.json"
        self._run_lock_path = cfg.data_dir / "run.lock"
        self.stop_file = cfg.data_dir / "EMERGENCY_STOP"

    @property
    def allowed_targets(self) -> list[str]:
        return sorted({t for s in self.catalog.values() for t in s.allowed_targets})

    # ---------- persisted state ----------
    def _read_state(self) -> dict:
        if self._state_path.exists():
            try:
                st = json.loads(_read_text(self._state_path))
                if isinstance(st, dict) and "latched" in st:
                    st.setdefault("history", [])
                    return st
            except (OSError, ValueError):
                pass
            return {"latched": True, "reason": "safety_state.json unreadable; treat as unsafe", "since": None, "history": []}
        return {"latched": False, "reason": None, "since": None, "history": []}

    @property
    def latched(self) -> bool:
        return bool(self._read_state()["latched"])

    def latch(self, reason: str) -> None:
        with self._lock:
            st = self._read_state()
            if not st["latched"] or st.get("since") is None:
                st.update(latched=True, reason=reason, since=utcnow().isoformat())
            st["history"].append({"at": utcnow().isoformat(), "event": "latched", "reason": reason})
            _atomic_write(self._state_path, st)

    def status(self) -> dict:
        st = self._read_state()
        holder = self.lock_holder()
        busy = self._active_run or (holder is not None and holder.get("alive"))
        return {
            "state": "LATCHED_UNSAFE" if st["latched"] else ("BUSY" if busy else "READY"),
            "latched_reason": st["reason"],
            "latched_since": st["since"],
            "active_run": self._active_run or (holder or {}).get("run_id"),
            "run_lock": holder,
            "emergency_stop_engaged": self.stop_file.exists(),
            "fault_marker": self.active_fault_marker(),
        }

    # ---------- cross-process run lock ----------
    def lock_holder(self) -> dict | None:
        """Who holds run.lock: {"pid", "run_id", "alive"}; alive=False means a crashed process left it behind."""
        if not self._run_lock_path.exists():
            return None
        try:
            info = json.loads(_read_text(self._run_lock_path))
            pid = int(info.get("pid", -1))
        except (OSError, ValueError, TypeError):
            return {"pid": None, "run_id": None, "alive": False, "corrupt": True}
        return {"pid": pid, "run_id": info.get("run_id"), "alive": pid_alive(pid)}

    def other_process_active(self) -> bool:
        h = self.lock_holder()
        return h is not None and bool(h.get("alive")) and h.get("pid") != os.getpid()

    def _acquire_run_lock(self, run_id: str) -> None:
        try:
            fd = os.open(self._run_lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            h = self.lock_holder() or {}
            if h.get("alive"):
                raise Busy(f"experiment {h.get('run_id')} is active in process {h.get('pid')}") from None
            raise Latched(f"stale run lock from dead process {h.get('pid')} (run {h.get('run_id')}); "
                          "startup recovery + reset required") from None
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "run_id": run_id, "at": utcnow().isoformat()}, f)

    def _release_run_lock(self, run_id: str) -> None:
        h = self.lock_holder()
        if h is not None and h.get("pid") == os.getpid() and h.get("run_id") == run_id:  # decided once
            _unlink(self._run_lock_path)

    # ---------- validation ----------
    def validate(self, raw: object) -> ExperimentRequest:
        if not isinstance(raw, dict):
            raise RequestRejected("request must be a JSON object")
        try:
            req = ExperimentRequest.model_validate(raw)
        except ValidationError as exc:
            raise RequestRejected(f"invalid request: {exc.errors(include_url=False)}") from exc
        spec = self.catalog.get(req.experiment_type)
        if spec is None:
            raise RequestRejected(f"experiment type {req.experiment_type!r} is not in the catalogue")
        if req.target not in spec.allowed_targets:
            raise RequestRejected(f"target {req.target!r} not allowlisted for {req.experiment_type}")
        limit = min(self.cfg.max_duration_s, spec.max_duration_s, HARD_MAX_DURATION_S)
        if req.duration_s > limit:
            raise RequestRejected(f"duration_s {req.duration_s} exceeds limit {limit}")
        return req

    def verify_target(self, name: str) -> ContainerState:
        """Target must exist, run unpaused, and carry the sandbox compose-project label."""
        try:
            st = self.docker.inspect(name)
        except DockerError as exc:
            raise SafetyError(f"cannot inspect target: {exc}") from exc
        if not st.exists:
            raise SafetyError(f"target container {name!r} does not exist")
        if st.project != self.cfg.compose_project:
            raise SafetyError(f"target {name!r} belongs to project {st.project!r}, not sandbox {self.cfg.compose_project!r}")
        if not st.running or st.paused:
            raise SafetyError(f"target {name!r} not in a clean running state (status={st.status})")
        return st

    # ---------- run lifecycle ----------
    def begin(self, run_id: str) -> threading.Event:
        with self._lock:
            if self._active_run is not None:
                raise Busy(f"experiment {self._active_run} is already active")
            st = self._read_state()
            if st["latched"]:
                raise Latched(f"controller latched unsafe: {st['reason']}")
            if self.stop_file.exists():
                raise Latched("emergency stop engaged; run `reset` after checking the stack")
            self._acquire_run_lock(run_id)  # cross-process exclusion; raises Busy / Latched
            if self._marker_path.exists():
                self._release_run_lock(run_id)
                raise Latched("an active-fault marker exists from an interrupted run; run startup recovery + reset")
            self._active_run = run_id
            self._stop_event = threading.Event()
            return self._stop_event

    def end(self, run_id: str) -> None:
        with self._lock:
            if self._active_run == run_id:
                self._active_run = None
                self._stop_event = None
            self._release_run_lock(run_id)

    def stop_requested(self, ev: threading.Event) -> bool:
        return ev.is_set() or self.stop_file.exists()

    def signal_stop(self) -> None:
        """Lock-free and I/O-free: safe to call from a signal handler at any point."""
        ev = self._stop_event
        if ev is not None:
            ev.set()

    def emergency_stop(self, source: str) -> dict:
        """Signal the active run (in this or another process) to stop; persists so no new run starts until reset."""
        self.signal_stop()
        self.stop_file.write_text(json.dumps({"at": utcnow().isoformat(), "source": source}), encoding="utf-8")
        holder = self.lock_holder()
        return {"stopped_run": self._active_run or (holder or {}).get("run_id"), "source": source}

    # ---------- active-fault marker ----------
    def mark_fault_active(self, run_id: str, target: str, mechanism: str, container_id: str | None = None) -> None:
        _atomic_write(self._marker_path, {"run_id": run_id, "target": target, "mechanism": mechanism,
                                          "container_id": container_id, "pid": os.getpid(), "at": utcnow().isoformat()})

    def clear_fault_marker(self) -> None:
        _unlink(self._marker_path)

    def active_fault_marker(self) -> dict | None:
        if not self._marker_path.exists():
            return None
        try:
            m = json.loads(_read_text(self._marker_path))
            return m if isinstance(m, dict) else {"corrupt": True}
        except (OSError, ValueError):
            return {"corrupt": True}

    def startup_recovery(self) -> dict | None:
        """Called on process start. If a previous process died mid-fault, undo it and latch for human review.

        Does nothing while another live process holds the run lock: that run owns its marker.
        """
        if self.other_process_active():
            return None
        marker = self.active_fault_marker()
        holder = self.lock_holder()
        if marker is None and holder is None:
            return None
        outcomes = []
        # unknown/corrupt marker: check every allowlisted target, not just the named one
        targets = [marker["target"]] if marker and marker.get("target") in self.allowed_targets else self.allowed_targets
        for target in targets:
            try:
                st = self.docker.inspect(target)
                in_sandbox = st.exists and st.project == self.cfg.compose_project
                if in_sandbox and st.paused:
                    self.docker.unpause(target)
                    outcomes.append(f"{target}: unpaused")
                elif in_sandbox and not st.running:
                    # only undo a stop WE made: marker says docker_stop for this exact container id
                    ours = (marker is not None and marker.get("mechanism") == "docker_stop"
                            and marker.get("target") == target and marker.get("container_id") == st.id)
                    if ours:
                        self.docker.start(target)
                        outcomes.append(f"{target}: started")
                    else:
                        outcomes.append(f"{target}: stopped, NOT started (no matching docker_stop marker)")
                else:
                    outcomes.append(f"{target}: state {st.status}")
            except DockerError as exc:
                outcomes.append(f"{target}: rollback failed: {exc}")
        outcome = "; ".join(outcomes)
        run_id = (marker or {}).get("run_id") or (holder or {}).get("run_id")
        self.latch(f"interrupted run {run_id} found at startup ({outcome})")
        _unlink(self._run_lock_path)  # dead holder: lock is meaningless now; latch still blocks
        return {"marker": marker or {"run_id": run_id}, "outcome": outcome}

    def reset(self, operator: str, note: str) -> dict:
        """Clear a latch only if no run is active anywhere and every allowlisted target is running and unpaused."""
        if not operator.strip():
            raise SafetyError("reset requires an operator name")
        with self._lock:
            if self._active_run is not None or self.other_process_active():
                raise Busy("cannot reset while an experiment is active")
            problems = []
            for t in self.allowed_targets:
                try:
                    st = self.docker.inspect(t)
                except DockerError as exc:
                    problems.append(f"{t}: {exc}")
                    continue
                if not st.exists or not st.running or st.paused:
                    problems.append(f"{t}: status={st.status}")
            if problems:
                raise SafetyError(f"reset refused, targets not clean: {problems}")
            st = self._read_state()
            st["history"].append({"at": utcnow().isoformat(), "event": "reset", "operator": operator, "note": note,
                                  "previous_reason": st["reason"]})
            st.update(latched=False, reason=None, since=None)
            _atomic_write(self._state_path, st)
            self.clear_fault_marker()
            _unlink(self._run_lock_path)
            self.stop_file.unlink(missing_ok=True)
        return {"reset_by": operator, "targets_checked": self.allowed_targets}
