"""Thin local HTTP API + dashboard. Binds to 127.0.0.1 only.

It calls exactly the code paths the CLI uses:
  run   -> ExperimentRunner.start()        (validation, allowlist, one-experiment lock, watchdog)
  kill  -> SafetyController.emergency_stop()
  reset -> SafetyController.reset()
It adds no safety logic of its own. POST /api/run takes exactly {"experiment_id", "approved_by"}: the id must be a key of
the experiment catalogue and the approver a typed, non-blank name; target and duration are chosen here from the
catalogue (never from the client), and the request then goes through the same validation, lock and watchdog as the
CLI. GET /api/proposals lists the rule-based planner's ranking with reasons, predicted symptoms and risks.
GET /api/insights is read-only (scores, planner proposals, findings from stored run records).
"""
from __future__ import annotations

import json
import threading
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .config import REPO_ROOT, ExperimentSpec, SafetyConfig
from .evidence import EvidenceStore
from .observation import utcnow
from .orchestrator import ExperimentRunner
from .safety import Busy, Latched, RequestRejected, SafetyController, SafetyError
from .telemetry import FreshnessTracker

HOST = "127.0.0.1"  # never exposed beyond localhost
DEFAULT_PORT = 8765
DASHBOARD_DURATION_S = 12  # fault window for runs started from the dashboard (capped by catalogue and safety config)


class DashboardRun(BaseModel):
    """The only body POST /api/run accepts. No target, duration or any other parameter can be sent."""
    model_config = ConfigDict(extra="forbid", strict=True)
    experiment_id: str = Field(min_length=1, max_length=63)
    approved_by: str = Field(min_length=1, max_length=80)

    @field_validator("approved_by")
    @classmethod
    def _typed_name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v or not any(c.isalpha() for c in v):
            raise ValueError("approved_by must be the approver's typed name")
        return v


def dashboard_request(experiment_id: str, approved_by: str, catalog: dict[str, ExperimentSpec], max_duration_s: int,
                      proposal: dict | None = None, duration_s: int = DASHBOARD_DURATION_S) -> dict:
    """Build the ExperimentRequest for a dashboard pick. Target and duration come from the catalogue, never the client."""
    spec = catalog[experiment_id]
    predicted = "; ".join(spec.expected_symptoms)
    rank = f"planner rank {proposal['rank']}" if proposal else "not ranked"
    return {
        "experiment_type": experiment_id,
        "target": spec.allowed_targets[0],
        "duration_s": min(duration_s, spec.max_duration_s, max_duration_s),
        "approved_by": approved_by,
        "hypothesis": f"Dashboard pick ({rank}). Predicted: {predicted}"[:500],
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
               store: EvidenceStore, *, monitor: bool = True, dashboard_duration_s: int = DASHBOARD_DURATION_S,
               insight_sources: dict[str, Path] | None = None, dep_map_path: Path | None = None) -> FastAPI:
    live = LiveState(cfg.stale_threshold_s)
    runner = ExperimentRunner(cfg, catalog, ctl, docker, observer, store, on_event=live.on_event)
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
    app.state.runner, app.state.ctl, app.state.live = runner, ctl, live

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
            "last_run": _run_summary(_latest_finished_record()),
            "startup_recovery": getattr(app.state, "recovered", None),
        }

    @app.get("/api/runs/latest")
    def latest_run():
        rec = _latest_record()
        return rec if rec is not None else JSONResponse({"detail": "no runs recorded"}, status_code=404)

    # fixed names only: the query parameter never becomes a path
    sources = insight_sources or {"local": store.data_dir / "runs", "day2": REPO_ROOT / "reports" / "day2"}
    dep_map_file = dep_map_path or REPO_ROOT / "config" / "dependency_map.json"

    @app.get("/api/insights")
    def insights(source: str = "local"):
        if source not in sources:
            return JSONResponse({"detail": f"source must be one of {sorted(sources)}"}, status_code=422)
        from .insights import build_insights
        try:
            dep_map = json.loads(dep_map_file.read_text(encoding="utf-8"))
            out = build_insights(sources[source], catalog, dep_map, cfg.max_duration_s)
        except Exception as exc:  # noqa: BLE001 - display-only; never break the server or the controls
            return JSONResponse({"detail": f"insights unavailable: {type(exc).__name__}: {exc}"}, status_code=500)
        return {"source": source, "sources": sorted(sources), **out}

    def _proposals() -> list[dict]:
        """Rule-based planner ranking over the local evidence store, plus each experiment's predicted symptoms."""
        from .insights import catalog_dict
        from .planner import plan
        from .scoring import load_runs
        runs_dir = store.data_dir / "runs"
        runs = load_runs(runs_dir) if runs_dir.is_dir() else []
        dep_map = json.loads(dep_map_file.read_text(encoding="utf-8"))
        out = []
        for e in plan(catalog_dict(catalog), dep_map, runs, cfg.max_duration_s):
            spec = catalog[e["experiment_type"]]
            req = dashboard_request(e["experiment_type"], "-", catalog, cfg.max_duration_s, e, dashboard_duration_s)
            out.append({"experiment_id": e["experiment_type"], "rank": e["rank"], "priority": e["priority"],
                        "information": e["information"], "risk": e["risk"], "rationale": e["rationale"],
                        "target": req["target"], "duration_s": req["duration_s"], "mechanism": spec.mechanism,
                        "predicted_symptoms": list(spec.expected_symptoms), "risks": list(spec.risks),
                        "provider": "rule-based"})
        return out

    @app.get("/api/proposals")
    def proposals():
        try:
            return {"catalog": sorted(catalog), "proposals": _proposals()}
        except Exception as exc:  # noqa: BLE001 - display-only; never break the server or the controls
            return JSONResponse({"detail": f"proposals unavailable: {type(exc).__name__}: {exc}"}, status_code=500)

    @app.post("/api/run", status_code=202)
    async def run(request: Request):
        if request.query_params:
            return JSONResponse({"detail": "POST /api/run takes no query parameters"}, status_code=422)
        try:
            pick = DashboardRun.model_validate(json.loads(await request.body() or b"null"))
        except (ValueError, ValidationError) as exc:
            return JSONResponse({"detail": 'body must be exactly {"experiment_id": <catalogue id>, '
                                           f'"approved_by": <your name>}}: {exc}'[:600]}, status_code=422)
        if pick.experiment_id not in catalog:
            return JSONResponse({"detail": f"experiment_id must be one of {sorted(catalog)}"}, status_code=422)
        try:
            proposal = next((p for p in _proposals() if p["experiment_id"] == pick.experiment_id), None)
        except Exception:  # noqa: BLE001 - the ranking is context, never a precondition for the safety path
            proposal = None
        req = dashboard_request(pick.experiment_id, pick.approved_by, catalog, cfg.max_duration_s, proposal,
                                dashboard_duration_s)
        try:
            run_id = runner.start(req)
        except Busy as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        except Latched as exc:
            return JSONResponse({"detail": str(exc)}, status_code=423)
        except RequestRejected as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        try:  # what the approver saw when approving; stored next to the run record, never read by the safety path
            store.save_proposal(run_id, {"run_id": run_id, "approved_by": pick.approved_by, "approved_via": "dashboard",
                                         "approved_at": utcnow().isoformat(), "request": req, "proposal": proposal,
                                         "model_calls": [], "note": "Dashboard proposals come from the rule-based "
                                         "planner; no model is called when a run is started. Model calls made by "
                                         "`rp advise` are logged in data/ai_calls/."})
        except Exception:  # noqa: BLE001 - evidence for context only; the run is already under the safety controller
            pass
        return {"run_id": run_id, "request": req}

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
