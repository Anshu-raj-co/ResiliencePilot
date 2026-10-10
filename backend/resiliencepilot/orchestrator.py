"""Experiment orchestrator: VALIDATED -> PRECHECK -> INJECTING -> ACTIVE -> ROLLING_BACK -> VERIFYING_RECOVERY
-> COMPLETED | ABORTED | LATCHED_UNSAFE.

Rollback happens in `finally`, so it runs on normal completion, emergency stop, observer loss, a guardrail trip
(error rate, latency p95, blast radius) and any exception. The watchdog rolls back independently if the loop overruns
its deadline.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Protocol

from .config import HARD_MAX_DURATION_S, ExperimentSpec, SafetyConfig
from .docker_cli import Docker, DockerError
from .evidence import EvidenceStore
from .faults import Watchdog, make_fault
from .guardrails import BlastRadiusMonitor, ErrorRateGuard, LatencyGuard, blast_radius_changes, latency_threshold, p95
from .observation import Observation, utcnow
from .safety import ExperimentRequest, SafetyController, SafetyError
from .telemetry import Freshness, FreshnessTracker

RECOVERY_CRITERIA = ("rollback verified by docker inspect ({restored}) AND {n} successive advances of "
                     "createdDateTime beyond both the last pre-rollback value and the fms-server time of the last fault-window "
                     "poll, each with HTTP 200 and data_age_s <= {thr}s")


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
        g = cfg.guardrails
        fault = make_fault(spec, self.docker, req.target)
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
                         "criteria": RECOVERY_CRITERIA.format(restored=fault.restored_description,
                                                              n=cfg.recovery_consecutive, thr=cfg.stale_threshold_s),
                         "detail": None},
            "impact": {},
            "guardrails": {"config": {**vars(g), "max_error_rate": 0.0 if spec.expected_statuses  # D-015: zero tolerance
                                      else spec.max_error_rate if spec.max_error_rate is not None else g.max_error_rate},
                           "blast_radius_allowed": list(spec.effective_blast_radius), "triggered": None},
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

        def observe(phase: str, timeout_s: float | None = None):
            o = self.observer.observe() if timeout_s is None else self.observer.observe(timeout_s=timeout_s)
            a = tracker.update(o)
            entry = {"phase": phase, "t_s": round(time.monotonic() - mono0, 3), "observation": o.to_dict(),
                     "assessment": a.to_dict()}
            with rec_lock:
                rec["observations"].append(entry)
            phase_obs[phase].append((o, a))
            self.on_event("observation", entry)
            return o, a

        watchdog: Watchdog | None = None
        monitor: BlastRadiusMonitor | None = None
        br_ids: list[str] = []
        br_baseline: dict = {}
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
            target_state = self.ctl.verify_target(req.target)
            fault.bind_precheck(target_state)
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

            # ---- GUARDRAIL BASELINES: refuse to inject if we cannot watch the blast radius ----
            stored = self.store.latest_baseline()
            pre_lat = [o.latency_ms for o, _ in phase_obs["precheck"] if o.latency_ms is not None]
            if stored is not None:
                base_p95, base_src = stored[1]["latency_ms"]["p95"], f"baseline {stored[0]}"
            else:
                base_p95, base_src = p95(pre_lat), "precheck polls (no stored baseline)"
            thr_ms = latency_threshold(base_p95, g.latency_p95_multiplier, g.latency_p95_floor_ms)
            thr_src = "baseline"
            if spec.fault_latency_p95_max_ms is not None:  # declared envelope for a slow-failing dependency
                thr_ms, thr_src = spec.fault_latency_p95_max_ms, "experiment envelope (fault window only)"
            try:
                br_ids = self.docker.project_container_ids(cfg.compose_project)
                br_baseline = self.docker.snapshot(br_ids)
            except DockerError as exc:
                raise SafetyError(f"blast radius not observable before injection: {exc}") from exc
            if req.target not in br_baseline:
                raise SafetyError("blast-radius baseline is empty or does not contain the target; not injecting")
            allowed = set(spec.effective_blast_radius)
            rec["guardrails"].update(
                latency_baseline_p95_ms=base_p95, latency_baseline_source=base_src, latency_threshold_ms=round(thr_ms, 2),
                latency_threshold_source=thr_src, fault_observer_timeout_s=spec.fault_observer_timeout_s or cfg.http_timeout_s,
                blast_radius_baseline={n: e.status for n, e in sorted(br_baseline.items())})
            err_guard = ErrorRateGuard(rec["guardrails"]["config"]["max_error_rate"], g.error_min_samples,
                                       expected=spec.expected_statuses)
            rec["guardrails"]["expected_statuses"] = list(spec.expected_statuses)
            # with a declared expected symptom, the API must keep ANSWERING: the first lost poll aborts
            loss_limit = 1 if spec.expected_statuses else cfg.observer_loss_max_consecutive
            lat_guard = LatencyGuard(thr_ms, g.latency_window, g.latency_min_samples)
            event("guardrails_armed", {"latency_threshold_ms": round(thr_ms, 2), "latency_baseline": base_src,
                                       "max_error_rate": err_guard.max_error_rate,
                                       "expected_statuses": list(spec.expected_statuses),
                                       "blast_radius_allowed": sorted(allowed),
                                       "watched_containers": len(br_baseline)})

            # ---- INJECT ----
            self.ctl.mark_fault_active(run_id, req.target, fault.mechanism,  # before the fault: crash-recoverable
                                       container_id=target_state.id)
            event("injecting", {"target": req.target, "mechanism": fault.mechanism}, state="INJECTING")
            inject_mono = time.monotonic()
            rec["impact"]["injected_at"] = utcnow().isoformat()
            # total fault time is capped at 30 s even including grace (brief: hard max 30 s)
            watchdog = Watchdog(inject_mono + min(req.duration_s + cfg.watchdog_grace_s, HARD_MAX_DURATION_S),
                                watchdog_fire)
            watchdog.start()
            monitor = BlastRadiusMonitor(self.docker, cfg.compose_project, br_ids, br_baseline, allowed,
                                         g.blast_radius_interval_s, g.blast_radius_max_snapshot_failures)
            monitor.start()
            fault.inject()
            if not fault.verify_injected():
                rec["stop_reason"] = "fault_not_verified"
                event("fault_not_verified", {"target": req.target})
                return rec  # finally{} rolls back and records outcome
            rec["impact"]["fault_verified"] = True
            rec["impact"]["injection_latency_s"] = round(time.monotonic() - inject_mono, 3)
            active = {"verified_by": fault.verified_by, "injection_latency_s": rec["impact"]["injection_latency_s"]}
            if getattr(fault, "exit_code", None) is not None:
                active["exit_code"] = fault.exit_code
            event("fault_active", active, state="ACTIVE")

            # ---- ACTIVE: observe until duration, stop, or safety condition ----
            losses = 0
            first_expected = None
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
                if monitor.tripped.is_set():
                    rec["stop_reason"] = "guardrail_blast_radius"
                    self._trip(rec, monitor.violation, mono0, event)
                    break
                poll_timeout = spec.fault_observer_timeout_s
                if poll_timeout is not None:
                    # never START a poll that could still be waiting after the fault window: the watchdog is a backstop,
                    # not the normal end of a run
                    left = req.duration_s - (time.monotonic() - inject_mono)
                    if left < poll_timeout:
                        time.sleep(max(0.0, min(cfg.poll_interval_s, left)))
                        continue
                o, a = observe("fault", poll_timeout)
                tripped = err_guard.update(o.http_status) or lat_guard.update(o.latency_ms)
                if tripped is not None:
                    rec["stop_reason"] = {"error_rate": "guardrail_error_rate",
                                          "latency_p95": "guardrail_latency"}[tripped.guard]
                    self._trip(rec, tripped, mono0, event)
                    break
                if o.http_status in spec.expected_statuses and first_expected is None:
                    first_expected = round(time.monotonic() - inject_mono, 3)
                    rec["impact"]["time_to_first_expected_status_s"] = first_expected
                    event("expected_symptom", {"time_s": first_expected, "http_status": o.http_status})
                if a.freshness is Freshness.OBSERVER_ERROR and o.http_status is None:  # no response at all
                    losses += 1
                    if losses >= loss_limit:
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
            if monitor is not None:
                monitor.stop()
                monitor.join(timeout=2.0)
            self._finish(rec, req, run_id, fault, watchdog, tracker, phase_obs, inject_mono, ttd, event, observe,
                         br_ids, br_baseline)
        return rec

    @staticmethod
    def _trip(rec: dict, violation, mono0: float, event) -> None:
        rec["guardrails"]["triggered"] = {"guard": violation.guard, "detail": violation.detail,
                                          "t_s": round(time.monotonic() - mono0, 3)}
        event("guardrail_triggered", {"guard": violation.guard, **violation.detail})

    def _measure_data_gap(self, rec: dict, event) -> None:
        """Evidence only (never changes the outcome): which statuses did the backend actually STORE around the fault?
        One read-only window query from 5 s before injection until now."""
        query = getattr(self.observer, "statuses_between", None)
        injected_at = rec["impact"].get("injected_at")
        if query is None or injected_at is None:
            return
        try:
            self._data_gap_unsafe(rec, event, query, injected_at)
        except Exception as exc:  # noqa: BLE001 - evidence collection must never affect safety or the outcome
            rec["data_gap"] = {"error": repr(exc)}

    @staticmethod
    def _data_gap_unsafe(rec: dict, event, query, injected_at: str) -> None:
        from datetime import datetime, timedelta
        start = datetime.fromisoformat(injected_at) - timedelta(seconds=5)
        end = utcnow()
        created = query(start, end)
        gaps = [(b - a).total_seconds() for a, b in zip(created, created[1:])]
        biggest = max(range(len(gaps)), key=gaps.__getitem__) if gaps else None
        rec["data_gap"] = {
            "window_start": start.isoformat(), "window_end": end.isoformat(), "stored_statuses": len(created),
            "max_gap_s": round(gaps[biggest], 3) if gaps else None,
            "gap_from": created[biggest].isoformat() if gaps else None,
            "gap_to": created[biggest + 1].isoformat() if gaps else None,
            "median_interval_s": round(sorted(gaps)[len(gaps) // 2], 3) if gaps else None,
            "method": "GET /rfms/vehiclestatuses?starttime&stoptime (stored createdDateTime values)",
        }
        event("data_gap_measured", {k: rec["data_gap"][k] for k in ("stored_statuses", "max_gap_s", "median_interval_s")})

    def _final_blast_check(self, rec, br_ids, br_baseline) -> bool:
        """After recovery: nothing outside the blast radius may have changed during the whole run."""
        if not br_baseline:
            return True
        allowed = set(rec["guardrails"]["blast_radius_allowed"])
        for _ in range(2):  # one retry for a transient docker error; still unobservable -> not clean
            try:
                ids = sorted(set(br_ids) | set(self.docker.project_container_ids(self.cfg.compose_project)))
                changes = blast_radius_changes(br_baseline, self.docker.snapshot(ids), allowed)
                rec["guardrails"]["final_blast_check"] = {"clean": not changes, "changes": changes}
                return not changes
            except DockerError as exc:
                rec["guardrails"]["final_blast_check"] = {"clean": False, "error": str(exc)}
        return False

    def _finish(self, rec, req, run_id, fault, watchdog, tracker, phase_obs, inject_mono, ttd, event, observe,
                br_ids=(), br_baseline=None) -> None:
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
            fault_srv = [o.server_time for o, _ in phase_obs["fault"] if o.server_time is not None]
            if fault_srv:  # anything stamped before this instant may be an in-flight pre-fault message
                prev = max(prev, fault_srv[-1]) if prev is not None else fault_srv[-1]
            elif rec["impact"].get("injected_at"):
                # no fault poll carried a server time (e.g. every poll was an HTTP 500): bound recovery by the moment
                # the fault was verified (+0.5 s margin for host/VM clock skew) instead of the pre-fault value
                from datetime import datetime, timedelta
                bound = (datetime.fromisoformat(rec["impact"]["injected_at"])
                         + timedelta(seconds=(rec["impact"].get("injection_latency_s") or 0.0) + 0.5))
                prev = max(prev, bound) if prev is not None else bound
                rec["recovery"]["lower_bound"] = {"source": "verified injection time + 0.5 s", "at": bound.isoformat()}
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
            self._measure_data_gap(rec, event)
            if not self._final_blast_check(rec, list(br_ids), br_baseline or {}):
                self.ctl.latch(f"run {run_id}: containers outside the blast radius changed during the run")
                event("latched", {"final_blast_check": rec["guardrails"]["final_blast_check"]}, state="LATCHED_UNSAFE")
                return
            as_planned = rec["impact"].get("fault_verified") and rec["stop_reason"] == "duration_elapsed"
            if self.ctl.latched:  # e.g. the watchdog latched even though a later retry succeeded
                event("latched", "controller latched during run", state="LATCHED_UNSAFE")
                return
            final = "COMPLETED" if as_planned else "ABORTED"
            event("finished", {"stop_reason": rec["stop_reason"]}, state=final)
        except Exception as exc:  # noqa: BLE001 - never leave the system in an unknown state silently
            self.ctl.latch(f"run {run_id}: error during rollback/recovery: {exc!r}")
            event("latched", repr(exc), state="LATCHED_UNSAFE")
        finally:
            if watchdog is not None:
                watchdog.cancel()
            keep = {k: rec["impact"].get(k) for k in ("injection_latency_s", "injected_at", "time_to_first_expected_status_s")}
            inj_lat = keep["injection_latency_s"]
            rec["impact"] = _impact(rec["impact"].get("fault_verified", False), phase_obs, ttd)
            rec["impact"].update(keep)
            fault_polls = phase_obs["fault"]
            rec["impact"]["expected_status_observations"] = sum(
                1 for o, _ in fault_polls if o.http_status in set(rec["guardrails"].get("expected_statuses") or []))
            # silent = the failure was only visible in the data (HTTP 200 while stale), never as an HTTP error
            rec["impact"]["silent_failure"] = bool(rec["impact"]["healthy_but_stale_observations"]) and not any(
                o.http_status not in (None, 200) for o, _ in fault_polls)
            rec["impact"]["time_to_detection_from_verified_injection_s"] = (
                round(ttd - inj_lat, 3) if ttd is not None and inj_lat is not None else None)
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
        "time_to_detection_s": ttd,  # measured from the moment the fault command was issued
        "precheck_max_data_age_s": round(max(pre_ages), 3) if pre_ages else None,
        "max_data_age_s": round(max(ages), 3) if ages else None,
        "max_stalled_for_s": round(max(stalls), 3) if stalls else None,
        "http_ok_ratio_during_fault": round(sum(o.http_ok for o, _ in fault) / len(fault), 3) if fault else None,
        "healthy_but_stale_observations": sum(a.healthy_but_stale for _, a in fault),
        "http_error_rate_during_fault": (round(sum(1 for o, _ in fault if o.http_status not in (None, 200))
                                               / max(1, sum(1 for o, _ in fault if o.http_status is not None)), 3)
                                         if fault else None),
        "latency_p95_during_fault_ms": p95([o.latency_ms for o, _ in fault if o.latency_ms is not None]),
    }


def _new_run_id() -> str:
    return utcnow().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
