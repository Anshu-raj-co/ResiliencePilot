"""Thin local HTTP API + dashboard. Binds to 127.0.0.1 only.

It calls exactly the code paths the CLI uses:
  run   -> ExperimentRunner.start()        (validation, allowlist, one-experiment lock, watchdog)
  kill  -> SafetyController.emergency_stop()
  reset -> SafetyController.reset()
It adds no safety logic of its own and cannot change the experiment: POST /api/run takes no parameters.
"""
from __future__ import annotations

import threading
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import ExperimentSpec, SafetyConfig
from .evidence import EvidenceStore
from .observation import utcnow
from .orchestrator import ExperimentRunner
from .safety import Busy, Latched, RequestRejected, SafetyController, SafetyError
from .telemetry import FreshnessTracker

HOST = "127.0.0.1"  # never exposed beyond localhost
DEFAULT_PORT = 8765
DASHBOARD_TEMPLATE = {  # the single approved template; same hypothesis text as scripts/demo-day1.sh
    "experiment_type": "telemetry_stall",
    "target": "fms-forwarder",
    "duration_s": 12,
    "approved_by": "dashboard-operator",
    "hypothesis": "Pausing fms-forwarder stalls createdDateTime while the rFMS API keeps returning HTTP 200",
}
_STATIC = Path(__file__).parent / "static" / "dashboard.html"


class LiveState:
    """Latest read-only monitor observations + progress of the active run (from the runner's event hook)."""

    def __init__(self, threshold: float, history: int = 120) -> None:
        self.lock = threading.Lock()
        self.threshold = threshold
        self.latest: dict | None = None
        self.recent: deque = deque(maxlen=history)
        self.run: dict | None = None  # {"run_id", "state", "events": [{"event","at","t_s"}]}

    def on_event(self, kind: str, data: dict) -> None:
        if kind not in ("event", "finished"):
            return
        with self.lock:
            if self.run is None or self.run["run_id"] != data["run_id"]:
                self.run = {"run_id": data["run_id"], "state": None, "events": []}
            self.run["state"] = data.get("state", self.run["state"])
            if kind == "event":
                self.run["events"].append({"event": data["event"], "at": utcnow().isoformat()})

    def record(self, obs, assessment) -> None:
        point = {
            "observed_at": obs.observed_at.isoformat(), "http_status": obs.http_status,
            "created_at": obs.created_at.isoformat() if obs.created_at else None,
            "received_at": obs.received_at.isoformat() if obs.received_at else None,
            "data_age_s": None if assessment.data_age_s is None else round(assessment.data_age_s, 3),
            "freshness": assessment.freshness.value, "stale": assessment.freshness.value == "STALE",
            "healthy_but_stale": assessment.healthy_but_stale, "error": obs.error,
        }
        with self.lock:
            self.latest = point
            self.recent.append(point)


def _phase(ctl_status: dict, run: dict | None, active: bool) -> str:
    if ctl_status["state"] == "LATCHED_UNSAFE":
        return "latched"
    if run and active:
        names = {e["event"] for e in run["events"]}
        st = run["state"]
        if st == "VERIFYING_RECOVERY":
            return "verifying_recovery"
        if st == "ROLLING_BACK":
            return "rolling_back"
        if st in ("ACTIVE", "INJECTING"):
            return "stale_detected" if "stale_detected" in names else "fault_active"
        return "precheck"
    if run and run["state"] in ("COMPLETED", "ABORTED") and any(e["event"] == "recovery_verified" for e in run["events"]):
        return "recovered"
    return "normal"


def _run_summary(rec: dict | None) -> dict | None:
    if rec is None:
        return None
    tl = {}
    for e in rec["timeline"]:
        tl.setdefault(e["event"], e.get("t_s"))
    inj = tl.get("fault_active")
    rel = lambda ev: None if inj is None or tl.get(ev) is None else round(tl[ev] - inj, 3)  # noqa: E731
    return {
        "run_id": rec["run_id"], "state": rec["state"], "stop_reason": rec["stop_reason"],
        "approved_by": rec["request"]["approved_by"], "duration_s": rec["request"]["duration_s"],
        "fault_verified": rec["impact"].get("fault_verified"),
        "time_to_detection_s": rec["impact"].get("time_to_detection_s"),
        "max_data_age_s": rec["impact"].get("max_data_age_s"),
        "http_ok_ratio_during_fault": rec["impact"].get("http_ok_ratio_during_fault"),
        "healthy_but_stale_observations": rec["impact"].get("healthy_but_stale_observations"),
        "rollback_verified": rec["rollback"].get("verified"), "rollback_by": rec["rollback"].get("by"),
        "rollback_at_s": rel("rollback_result"),
        "recovery_verified": rec["recovery"].get("verified"),
        "time_to_first_fresh_s": rec["recovery"].get("time_to_first_fresh_s"),
        "time_to_verified_s": rec["recovery"].get("time_to_verified_s"),
        "fault_window_closed_at_s": rel("fault_window_closed"),
    }


def create_app(cfg: SafetyConfig, catalog: dict[str, ExperimentSpec], ctl: SafetyController, docker, observer,
               store: EvidenceStore, *, run_template: dict | None = None, monitor: bool = True) -> FastAPI:
    live = LiveState(cfg.stale_threshold_s)
    runner = ExperimentRunner(cfg, catalog, ctl, docker, observer, store, on_event=live.on_event)
    template = dict(run_template or DASHBOARD_TEMPLATE)
    stop_monitor = threading.Event()

    def monitor_loop() -> None:
        tracker = FreshnessTracker(cfg.stale_threshold_s)
        while not stop_monitor.is_set():
            try:
                o = observer.observe()
                live.record(o, tracker.update(o))
            except Exception:  # noqa: BLE001 - the monitor is display-only; never crash the server
                pass
            stop_monitor.wait(cfg.poll_interval_s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.recovered = ctl.startup_recovery()  # same as every CLI start
        if monitor:
            threading.Thread(target=monitor_loop, name="rp-monitor", daemon=True).start()
        yield
        stop_monitor.set()

    app = FastAPI(title="ResiliencePilot dashboard API", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.runner, app.state.ctl, app.state.live, app.state.template = runner, ctl, live, template

    def _latest_record() -> dict | None:
        ids = store.list_runs()
        return store.load_run(ids[-1]) if ids else None

    def _latest_finished_record() -> dict | None:
        """Newest run that has reached a final state; an in-progress record is not a 'last run'."""
        for rid in reversed(store.list_runs()):
            rec = store.load_run(rid)
            if rec is not None and rec.get("state") in ("COMPLETED", "ABORTED", "LATCHED_UNSAFE"):
                return rec
        return None

    def _ctl_status() -> dict:
        # Read controller state under the controller's own (re-entrant) lock, which begin()/end() hold while they
        # create/delete run.lock. On Windows a concurrent reader makes that delete fail with PermissionError and
        # leaves a stale lock (fail-safe, but stuck BUSY). Serialising our reads avoids that in-process race.
        # emergency_stop() does not take this lock, so Kill is never delayed by it.
        # While a run is active in this process, the orchestrator deletes/replaces controller files (marker, latch
        # state); answer from memory and open none of them, so our polling can never make those operations fail.
        with ctl._lock:
            active = ctl._active_run
            if active is not None:
                return {"state": "BUSY", "latched_reason": None, "latched_since": None, "active_run": active,
                        "run_lock": None, "emergency_stop_engaged": ctl.stop_file.exists(), "fault_marker": None}
            return ctl.status()

    def _blocked() -> bool:
        st = _ctl_status()
        return st["state"] == "LATCHED_UNSAFE" or st["emergency_stop_engaged"]

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return _STATIC.read_text(encoding="utf-8")

    @app.get("/api/status")
    def status() -> dict:
        st = _ctl_status()
        with live.lock:
            latest, recent = live.latest, list(live.recent)
            run = None if live.run is None else {**live.run, "events": list(live.run["events"])}
        active = st["state"] == "BUSY"
        return {
            "state": st["state"], "phase": _phase(st, run, active), "blocked": _blocked(),
            "latched_reason": st["latched_reason"], "emergency_stop_engaged": st["emergency_stop_engaged"],
            "active_run": st["active_run"], "stale_threshold_s": cfg.stale_threshold_s,
            "poll_interval_s": cfg.poll_interval_s, "latest": latest, "recent": recent, "current_run": run,
            "last_run": _run_summary(_latest_finished_record()), "template": template,
            "startup_recovery": getattr(app.state, "recovered", None),
        }

    @app.get("/api/runs/latest")
    def latest_run():
        rec = _latest_record()
        return rec if rec is not None else JSONResponse({"detail": "no runs recorded"}, status_code=404)

    @app.post("/api/run", status_code=202)
    async def run(request: Request):
        body = await request.body()
        if request.query_params or body.strip() not in (b"", b"{}", b"null"):
            return JSONResponse({"detail": "POST /api/run takes no parameters; it always runs the approved template"},
                                status_code=422)
        try:
            run_id = runner.start(dict(template))
        except Busy as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        except Latched as exc:
            return JSONResponse({"detail": str(exc)}, status_code=423)
        except RequestRejected as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        return {"run_id": run_id, "request": template}

    @app.post("/api/kill")
    def kill() -> dict:
        return ctl.emergency_stop("dashboard")

    @app.post("/api/reset")
    async def reset(request: Request):
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            payload = None
        if not isinstance(payload, dict) or payload.get("confirm") is not True:
            return JSONResponse({"detail": 'reset requires {"confirm": true}'}, status_code=400)
        if not _blocked():
            return JSONResponse({"detail": "not latched; nothing to reset"}, status_code=409)
        try:
            return ctl.reset("dashboard-operator", "reset from dashboard after operator confirmation")
        except Busy as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        except SafetyError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)

    return app


def serve(port: int = DEFAULT_PORT) -> None:
    import uvicorn

    from .config import load_experiment_catalog, load_safety_config
    from .docker_cli import DockerCLI
    from .sdv_adapter import RfmsObserver

    cfg = load_safety_config()
    catalog = load_experiment_catalog()
    docker = DockerCLI()
    observer = RfmsObserver(cfg.rfms_base_url, cfg.http_timeout_s)
    ctl = SafetyController(cfg, catalog, docker, observer=observer)
    app = create_app(cfg, catalog, ctl, docker, observer, EvidenceStore(cfg.data_dir))
    print(f"ResiliencePilot dashboard: http://{HOST}:{port}/  (localhost only; Ctrl+C to stop)")
    uvicorn.run(app, host=HOST, port=port, log_level="warning")
