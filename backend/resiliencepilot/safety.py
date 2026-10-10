"""Deterministic safety controller. Small on purpose; every rule here has a negative-path test.

Rules enforced:
  * strict request schema (unknown fields, wrong types, unknown experiment types rejected)
  * target must be allowlisted for the experiment type AND belong to the sandbox compose project
  * duration <= min(config max, catalogue max, 30 s)
  * exactly one active experiment
  * latched-unsafe state persists on disk and blocks all experiments until an explicit, checked reset
  * an "active fault" marker is written before injection so an interrupted process can be cleaned up
  * emergency stop: in-process event, or the EMERGENCY_STOP file written by scripts/kill-switch.sh
"""
from __future__ import annotations

import json
import os
import threading
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

    experiment_type: Literal["telemetry_stall"]
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


class SafetyController:
    def __init__(self, cfg: SafetyConfig, catalog: dict[str, ExperimentSpec], docker: Docker) -> None:
        self.cfg = cfg
        self.catalog = catalog
        self.docker = docker
        self._lock = threading.Lock()
        self._active_run: str | None = None
        self._stop_event: threading.Event | None = None
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = cfg.data_dir / "safety_state.json"
        self._marker_path = cfg.data_dir / "active_fault.json"
        self.stop_file = cfg.data_dir / "EMERGENCY_STOP"

    # ---------- persisted state ----------
    def _read_state(self) -> dict:
        if self._state_path.exists():
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        return {"latched": False, "reason": None, "since": None, "history": []}

    def _write_state(self, st: dict) -> None:
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
        os.replace(tmp, self._state_path)

    @property
    def latched(self) -> bool:
        return bool(self._read_state()["latched"])

    def latch(self, reason: str) -> None:
        with self._lock:
            st = self._read_state()
            if not st["latched"]:
                st.update(latched=True, reason=reason, since=utcnow().isoformat())
            st["history"].append({"at": utcnow().isoformat(), "event": "latched", "reason": reason})
            self._write_state(st)

    def status(self) -> dict:
        st = self._read_state()
        return {
            "state": "LATCHED_UNSAFE" if st["latched"] else ("BUSY" if self._active_run else "READY"),
            "latched_reason": st["reason"],
            "latched_since": st["since"],
            "active_run": self._active_run,
            "emergency_stop_engaged": self.stop_file.exists(),
            "fault_marker": self.active_fault_marker(),
        }

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
            st = self._read_state()
            if st["latched"]:
                raise Latched(f"controller latched unsafe: {st['reason']}")
            if self._active_run is not None:  # checked before the marker: the active run owns the marker
                raise Busy(f"experiment {self._active_run} is already active")
            if self.stop_file.exists():
                raise Latched("emergency stop engaged; run `reset` after checking the stack")
            if self._marker_path.exists():
                raise Latched("an active-fault marker exists from an interrupted run; run startup recovery + reset")
            self._active_run = run_id
            self._stop_event = threading.Event()
            return self._stop_event

    def end(self, run_id: str) -> None:
        with self._lock:
            if self._active_run == run_id:
                self._active_run = None
                self._stop_event = None

    def stop_requested(self, ev: threading.Event) -> bool:
        return ev.is_set() or self.stop_file.exists()

    def emergency_stop(self, source: str) -> dict:
        """Signal the active run to stop and roll back. Also persists the stop so no new run starts until reset."""
        self.stop_file.write_text(json.dumps({"at": utcnow().isoformat(), "source": source}), encoding="utf-8")
        with self._lock:
            ev, run = self._stop_event, self._active_run
        if ev is not None:
            ev.set()
        return {"stopped_run": run, "source": source}

    # ---------- active-fault marker ----------
    def mark_fault_active(self, run_id: str, target: str, mechanism: str) -> None:
        self._marker_path.write_text(json.dumps(
            {"run_id": run_id, "target": target, "mechanism": mechanism, "at": utcnow().isoformat()}), encoding="utf-8")

    def clear_fault_marker(self) -> None:
        self._marker_path.unlink(missing_ok=True)

    def active_fault_marker(self) -> dict | None:
        if self._marker_path.exists():
            return json.loads(self._marker_path.read_text(encoding="utf-8"))
        return None

    def startup_recovery(self) -> dict | None:
        """Called on process start. If a previous process died mid-fault, undo it and latch for human review."""
        marker = self.active_fault_marker()
        if marker is None:
            return None
        target = marker.get("target")
        outcome = "no action"
        allowed = {t for s in self.catalog.values() for t in s.allowed_targets}
        if target in allowed:
            try:
                st = self.docker.inspect(target)
                if st.exists and st.paused:
                    self.docker.unpause(target)
                    outcome = "unpaused"
                else:
                    outcome = f"target state {st.status}"
            except DockerError as exc:
                outcome = f"rollback failed: {exc}"
        self.latch(f"interrupted run {marker.get('run_id')} found at startup ({outcome})")
        return {"marker": marker, "outcome": outcome}

    def reset(self, operator: str, note: str) -> dict:
        """Clear a latch only if every allowlisted target is verifiably running and unpaused."""
        if not operator.strip():
            raise SafetyError("reset requires an operator name")
        with self._lock:
            if self._active_run is not None:
                raise Busy("cannot reset while an experiment is active")
        targets = sorted({t for s in self.catalog.values() for t in s.allowed_targets})
        problems = []
        for t in targets:
            try:
                st = self.docker.inspect(t)
            except DockerError as exc:
                problems.append(f"{t}: {exc}")
                continue
            if not st.exists or not st.running or st.paused:
                problems.append(f"{t}: status={st.status}")
        if problems:
            raise SafetyError(f"reset refused, targets not clean: {problems}")
        with self._lock:
            st = self._read_state()
            st["history"].append({"at": utcnow().isoformat(), "event": "reset", "operator": operator, "note": note,
                                  "previous_reason": st["reason"]})
            st.update(latched=False, reason=None, since=None)
            self._write_state(st)
            self.clear_fault_marker()
            self.stop_file.unlink(missing_ok=True)
        return {"reset_by": operator, "targets_checked": targets}
