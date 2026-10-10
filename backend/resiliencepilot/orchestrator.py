"""Experiment orchestrator: VALIDATED -> PRECHECK -> INJECTING -> ACTIVE -> ROLLING_BACK -> VERIFYING_RECOVERY
-> COMPLETED | ABORTED | LATCHED_UNSAFE.

Rollback happens in `finally`, so it runs on normal completion, emergency stop, observer loss, safety-threshold
violation and any exception. The watchdog rolls back independently if the loop overruns its deadline.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Protocol

from .config import ExperimentSpec, SafetyConfig
from .docker_cli import Docker
from .evidence import EvidenceStore
from .faults import PauseFault, Watchdog
from .observation import Observation, utcnow
from .safety import ExperimentRequest, SafetyController, SafetyError
from .telemetry import Freshness, FreshnessTracker

RECOVERY_CRITERIA = ("rollback verified by docker inspect (running, not paused) AND {n} successive advances of "
                     "createdDateTime beyond the last pre-rollback value, each with HTTP 200 and data_age_s <= {thr}s")


class Observer(Protocol):
    def observe(self) -> Observation: ...


EventHook = Callable[[str, dict], None]


class ExperimentRunner:
    def __init__(self, cfg: SafetyConfig, catalog: dict[str, ExperimentSpec], controller: SafetyController,
                 docker: Docker, observer: Observer, store: EvidenceStore, on_event: EventHook | None = None) -> None:
        self.cfg, self.catalog, self.ctl = cfg, catalog, controller
        self.docker, self.observer, self.store = docker, observer, store
        self.on_event = on_event or (lambda kind, data: None)
        self.threads: dict[str, threading.Thread] = {}

    # ---------- entry points ----------
    def run(self, raw_request: object) -> dict:
        """Validate, acquire the single-experiment lock, execute synchronously. Raises SafetyError on refusal."""
        req = self.ctl.validate(raw_request)
        run_id = _new_run_id()
        stop_event = self.ctl.begin(run_id)
        try:
            return self._execute(req, run_id, stop_event)
        finally:
            self.ctl.end(run_id)

    def start(self, raw_request: object) -> str:
        """Same checks as run(), then execute in a background thread (used by the API)."""
        req = self.ctl.validate(raw_request)
        run_id = _new_run_id()
        stop_event = self.ctl.begin(run_id)

        def target() -> None:
            try:
                self._execute(req, run_id, stop_event)
            finally:
                self.ctl.end(run_id)

        t = threading.Thread(target=target, name=f"rp-run-{run_id}", daemon=True)
        self.threads[run_id] = t
        t.start()
        return run_id

    # ---------- the state machine ----------
    def _execute(self, req: ExperimentRequest, run_id: str, stop_event: threading.Event) -> dict:
        cfg, spec = self.cfg, self.catalog[req.experiment_type]
        mono0 = time.monotonic()
        rec_lock = threading.Lock()
        rec: dict = {
            "run_id": run_id, "request": req.model_dump(), "state": "VALIDATED",
            "spec": {"mechanism": spec.mechanism, "expected_symptoms": list(spec.expected_symptoms), "risks": list(spec.risks)},
            "limits": {"stale_threshold_s": cfg.stale_threshold_s, "poll_interval_s": cfg.poll_interval_s,
                       "watchdog_grace_s": cfg.watchdog_grace_s, "recovery_timeout_s": cfg.recovery_timeout_s},
            "timeline": [], "observations": [], "stop_reason": None,
            "rollback": {"attempted": False, "verified": False, "by": None, "detail": None},
            "recovery": {"verified": False, "time_to_first_fresh_s": None, "time_to_verified_s": None,
                         "criteria": RECOVERY_CRITERIA.format(n=cfg.recovery_consecutive, thr=cfg.stale_threshold_s),
                         "detail": None},
            "impact": {},
        }

        def event(name: str, detail: object = None, state: str | None = None) -> None:
            """Record a timeline event. Never raises: evidence I/O must not be able to block a safety action."""
            with rec_lock:
                if state:
                    rec["state"] = state
                rec["timeline"].append({"at": utcnow().isoformat(), "t_s": round(time.monotonic() - mono0, 3),
                                        "event": name, "detail": detail, "state": rec["state"]})
                try:
                    self.store.save_run(rec)
                except Exception as exc:  # noqa: BLE001
                    rec.setdefault("evidence_errors", []).append(repr(exc))
            try:
                self.on_event("event", {"run_id": run_id, "event": name, "detail": detail, "state": rec["state"]})
            except Exception:  # noqa: BLE001
                pass

        tracker = FreshnessTracker(cfg.stale_threshold_s)
        phase_obs: dict[str, list] = {"precheck": [], "fault": [], "recovery": []}

        def observe(phase: str):
            o = self.observer.observe()
            a = tracker.update(o)
            entry = {"phase": phase, "t_s": round(time.monotonic() - mono0, 3), "observation": o.to_dict(),
                     "assessment": a.to_dict()}
            with rec_lock:
                rec["observations"].append(entry)
            phase_obs[phase].append((o, a))
            self.on_event("observation", entry)
            return o, a

        fault = PauseFault(self.docker, req.target)
        watchdog: Watchdog | None = None
        inject_mono: float | None = None
        ttd: float | None = None

        def watchdog_fire() -> None:
            # safety actions first, bookkeeping second
            stop_event.set()
            try:
                res = fault.rollback(by="watchdog")
            except Exception as exc:  # noqa: BLE001
                self.ctl.latch(f"run {run_id}: watchdog rollback raised {exc!r}")
                raise
            if not res.verified:
                self.ctl.latch(f"run {run_id}: watchdog rollback not verified: {res.detail}")
            event("watchdog_fired", {"deadline_s": round(watchdog.deadline_mono - mono0, 3), "rollback": vars(res)})

        event("validated", {"approved_by": req.approved_by, "hypothesis": req.hypothesis})
        try:
            # ---- PRECHECK: never inject into an already-degraded or non-sandbox system ----
            event("precheck_start", state="PRECHECK")
            self.ctl.verify_target(req.target)
            fresh_streak, seen_advance = 0, False
            deadline = time.monotonic() + max(5.0, cfg.precheck_fresh_consecutive * cfg.poll_interval_s * 6)
            while not (fresh_streak >= cfg.precheck_fresh_consecutive and seen_advance):
                if self.ctl.stop_requested(stop_event):
                    rec["stop_reason"] = "emergency_stop"
                    raise SafetyError("emergency stop during precheck")
                if time.monotonic() > deadline:
                    raise SafetyError("telemetry not fresh and advancing before injection")
                _, a = observe("precheck")
                fresh_streak = fresh_streak + 1 if a.freshness is Freshness.FRESH else 0
                seen_advance = seen_advance or a.advanced
                time.sleep(cfg.poll_interval_s)
            event("precheck_passed", {"fresh_samples": fresh_streak})

            # ---- INJECT ----
            self.ctl.mark_fault_active(run_id, req.target, fault.mechanism)  # before the fault: crash-recoverable
            event("injecting", {"target": req.target, "mechanism": fault.mechanism}, state="INJECTING")
            inject_mono = time.monotonic()
            watchdog = Watchdog(inject_mono + req.duration_s + cfg.watchdog_grace_s, watchdog_fire)
            watchdog.start()
            fault.inject()
            if not fault.verify_injected():
                rec["stop_reason"] = "fault_not_verified"
                event("fault_not_verified", {"target": req.target})
                return rec  # finally{} rolls back and records outcome
            rec["impact"]["fault_verified"] = True
            event("fault_active", {"verified_by": "docker inspect State.Paused == true"}, state="ACTIVE")

            # ---- ACTIVE: observe until duration, stop, or safety condition ----
            losses = 0
            while True:
                if watchdog.fired.is_set():
                    rec["stop_reason"] = "watchdog_timeout"
                    break
                if self.ctl.stop_requested(stop_event):
                    rec["stop_reason"] = "emergency_stop"
                    break
                elapsed = time.monotonic() - inject_mono
                if elapsed >= req.duration_s:
                    rec["stop_reason"] = "duration_elapsed"
                    break
                o, a = observe("fault")
                if a.freshness is Freshness.OBSERVER_ERROR:
                    if o.http_status is not None:  # API answered non-200: blast radius larger than expected
                        rec["stop_reason"] = "safety_threshold"
                        event("safety_threshold", {"http_status": o.http_status})
                        break
                    losses += 1
                    if losses >= cfg.observer_loss_max_consecutive:
                        rec["stop_reason"] = "observer_lost"
                        event("observer_lost", {"consecutive_failures": losses})
                        break
                else:
                    losses = 0
                if a.freshness is Freshness.STALE and ttd is None:
                    ttd = round(time.monotonic() - inject_mono, 3)
                    event("stale_detected", {"time_to_detection_s": ttd, "http_status": o.http_status,
                                             "data_age_s": a.data_age_s, "healthy_but_stale": a.healthy_but_stale})
                time.sleep(max(0.0, min(cfg.poll_interval_s, req.duration_s - (time.monotonic() - inject_mono))))
            event("fault_window_closed", {"stop_reason": rec["stop_reason"]})
        except SafetyError as exc:
            rec["stop_reason"] = rec["stop_reason"] or "precheck_failed"
            event("refused", str(exc))
        except Exception as exc:  # noqa: BLE001 - any failure must still reach rollback
            rec["stop_reason"] = rec["stop_reason"] or "error"
            event("exception", repr(exc))
        finally:
            self._finish(rec, req, run_id, fault, watchdog, tracker, phase_obs, inject_mono, ttd, event, observe)
        return rec

    def _finish(self, rec, req, run_id, fault, watchdog, tracker, phase_obs, inject_mono, ttd, event, observe) -> None:
        cfg = self.cfg
        try:
            if not fault.inject_attempted:
                self.ctl.clear_fault_marker()
                event("aborted_without_fault", state="ABORTED")
                return

            event("rolling_back", state="ROLLING_BACK")
            res = fault.rollback(by="orchestrator")
            if watchdog is not None:
                watchdog.cancel()
            rec["rollback"] = vars(res)
            event("rollback_result", vars(res))
            if not res.verified:
                self.ctl.latch(f"run {run_id}: rollback not verified: {res.detail}")
                event("latched", "rollback not verified", state="LATCHED_UNSAFE")
                return
            self.ctl.clear_fault_marker()
            rollback_mono = time.monotonic()

            # ---- VERIFYING_RECOVERY: independent of the fault adapter's own claim ----
            event("verifying_recovery", state="VERIFYING_RECOVERY")
            prev = tracker.last_created
            advances, first_fresh = 0, None
            deadline = rollback_mono + cfg.recovery_timeout_s
            while time.monotonic() < deadline:
                o, a = observe("recovery")
                fresh = o.http_ok and o.created_at is not None and a.data_age_s is not None and a.data_age_s <= cfg.stale_threshold_s
                if fresh and (prev is None or o.created_at > prev):
                    advances += 1
                    prev = o.created_at
                    if first_fresh is None:
                        first_fresh = round(time.monotonic() - rollback_mono, 3)
                elif not fresh:
                    advances = 0
                if advances >= cfg.recovery_consecutive:
                    st = self.docker.inspect(req.target)
                    if st.running and not st.paused:
                        rec["recovery"].update(verified=True, time_to_first_fresh_s=first_fresh,
                                               time_to_verified_s=round(time.monotonic() - rollback_mono, 3),
                                               detail=f"{advances} fresh advances observed")
                        break
                time.sleep(cfg.poll_interval_s)
            if not rec["recovery"]["verified"]:
                rec["recovery"]["detail"] = f"criteria not met within {cfg.recovery_timeout_s}s (advances={advances})"
                rec["recovery"]["time_to_first_fresh_s"] = first_fresh
                self.ctl.latch(f"run {run_id}: recovery not verified")
                event("latched", "recovery not verified", state="LATCHED_UNSAFE")
                return
            event("recovery_verified", rec["recovery"])
            final = "COMPLETED" if rec["impact"].get("fault_verified") else "ABORTED"
            event("finished", {"stop_reason": rec["stop_reason"]}, state=final)
        except Exception as exc:  # noqa: BLE001 - never leave the system in an unknown state silently
            self.ctl.latch(f"run {run_id}: error during rollback/recovery: {exc!r}")
            event("latched", repr(exc), state="LATCHED_UNSAFE")
        finally:
            if watchdog is not None:
                watchdog.cancel()
            rec["impact"] = _impact(rec["impact"].get("fault_verified", False), phase_obs, ttd)
            self.store.save_run(rec)
            self.on_event("finished", {"run_id": run_id, "state": rec["state"]})


def _impact(fault_verified: bool, phase_obs: dict, ttd: float | None) -> dict:
    fault = phase_obs["fault"]
    ages = [a.data_age_s for _, a in fault if a.data_age_s is not None]
    stalls = [a.stalled_for_s for _, a in fault if a.stalled_for_s is not None]
    pre_ages = [a.data_age_s for _, a in phase_obs["precheck"] if a.data_age_s is not None]
    return {
        "fault_verified": fault_verified,
        "fault_observations": len(fault),
        "stale_detected": ttd is not None,
        "time_to_detection_s": ttd,
        "precheck_max_data_age_s": round(max(pre_ages), 3) if pre_ages else None,
        "max_data_age_s": round(max(ages), 3) if ages else None,
        "max_stalled_for_s": round(max(stalls), 3) if stalls else None,
        "http_ok_ratio_during_fault": round(sum(o.http_ok for o, _ in fault) / len(fault), 3) if fault else None,
        "healthy_but_stale_observations": sum(a.healthy_but_stale for _, a in fault),
    }


def _new_run_id() -> str:
    return utcnow().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
