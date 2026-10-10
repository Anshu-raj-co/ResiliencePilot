"""Operator CLI.  python -m resiliencepilot <command>

  status                     safety controller state + target containers
  observe [-n N]             poll the rFMS API and show freshness (read-only)
  baseline [-n N] [-i S]     collect and save a baseline
  run --duration S --approved-by NAME [--hypothesis TEXT] [--yes]
                             run the telemetry_stall experiment (asks for approval unless --yes)
  stop                       emergency stop (works from another terminal)
  reset --operator NAME --note TEXT
  runs | show RUN_ID [--json]   list runs / impact chart + timeline of one run
  deps                       verified dependency map with a live shared-network check
  show-baseline [FILE]       print a stored baseline (default: newest)
  report [--run ID] [--out FILE]  read-only Day-1 explainer HTML (default: latest full-length COMPLETED run)
  serve [--port N]           live dashboard + local API on http://127.0.0.1:N/ (localhost only)
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time

from .baseline import collect_baseline
from .config import REPO_ROOT, load_experiment_catalog, load_safety_config
from .docker_cli import DockerCLI
from .evidence import EvidenceStore
from .observation import utcnow
from .orchestrator import ExperimentRunner
from .safety import SafetyController, SafetyError
from .sdv_adapter import RfmsObserver
from .telemetry import FreshnessTracker
from .views import C, age_bar, load_dependency_map, render_baseline, render_dependency_map, render_impact_chart

_THRESHOLD = load_safety_config().stale_threshold_s


def _fmt_obs(entry: dict) -> str:
    o, a = entry["observation"], entry["assessment"]
    color = {"FRESH": C["g"], "STALE": C["r"]}.get(a["freshness"], C["y"])
    http = o["http_status"] if o["http_status"] is not None else "ERR"
    created = (o["created_at"] or "-")[11:23]
    received = (o["received_at"] or "-")[11:23]
    flag = f"  {C['r']}{C['b']}<- HTTP 200 but STALE{C['x']}" if a["healthy_but_stale"] else ""
    age = a["data_age_s"]
    age_txt = "" if age is None else f"{age:5.2f}s"
    return (f"  [{entry['phase']:>8} t={entry['t_s']:6.2f}s] HTTP {http}  created={created}  received={received}  "
            f"{age_bar(age, _THRESHOLD)} {age_txt}  {color}{a['freshness']}{C['x']}{flag}")


def _secs(value) -> str:
    """Seconds for display; 'n/a' when the event never happened (e.g. no stale detected before an emergency stop)."""
    return "n/a" if value is None else f"{value}s"


def _result_lines(rec: dict) -> list[str]:
    imp, rb, rc = rec["impact"], rec["rollback"], rec["recovery"]
    return [
        f"  fault verified: {imp.get('fault_verified')}   stale detected: {imp.get('stale_detected')} "
        f"(time to detection {_secs(imp.get('time_to_detection_s'))})",
        f"  during fault: HTTP-200 ratio {imp.get('http_ok_ratio_during_fault')}, max data age {_secs(imp.get('max_data_age_s'))}, "
        f"{imp.get('healthy_but_stale_observations')} healthy-but-stale observations",
        f"  rollback: verified={rb['verified']} by={rb['by']}   recovery: verified={rc['verified']} "
        f"first fresh after {_secs(rc['time_to_first_fresh_s'])}, verified after {_secs(rc['time_to_verified_s'])}",
    ]


def _printer(kind: str, data: dict) -> None:
    if kind == "observation":
        print(_fmt_obs(data), flush=True)
    elif kind == "event":
        detail = "" if data["detail"] in (None, {}) else f"  {C['d']}{json.dumps(data['detail'], default=str)}{C['x']}"
        print(f"{C['b']}» {data['event']}{C['x']}  [{data['state']}]{detail}", flush=True)


def _load_conditions(docker, cfg, summary: dict, poll_interval_s: float) -> dict:
    """What the system was doing while the baseline was taken. All values read live from Docker/config."""
    fwd, csv = docker.env("fms-forwarder"), docker.env("csv-provider")
    csv_file = REPO_ROOT / "Fleet Management" / "fleet-management" / "csv-provider" / "signalsFmsRecording.csv"
    rows = None
    if csv_file.exists():
        with csv_file.open(encoding="utf-8") as f:
            rows = sum(1 for _ in f) - 1  # minus header
    mem = docker.mem_total_bytes()
    return {
        "forwarder_timer_interval": fwd.get("TIMER_INTERVAL"),
        "csv_provider_infinite": csv.get("PROVIDER_INFINITE"),
        "csv_rows": rows,
        "vehicles": len(summary.get("vins", [])),
        "observer_poll_interval_s": poll_interval_s,
        "other_clients": "not measured (ResiliencePilot starts no load generator; Grafana polls only while a browser has it open)",
        "docker_mem_total_gib": round(mem / 2**30, 2) if mem else None,
        "stack_stats": docker.stats(cfg.compose_project),
    }


def build(on_event=None):
    cfg = load_safety_config()
    catalog = load_experiment_catalog()
    docker = DockerCLI()
    ctl = SafetyController(cfg, catalog, docker)
    observer = RfmsObserver(cfg.rfms_base_url, cfg.http_timeout_s)
    store = EvidenceStore(cfg.data_dir)
    return cfg, catalog, docker, ctl, observer, store, ExperimentRunner(cfg, catalog, ctl, docker, observer, store, on_event)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="resiliencepilot")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    so = sub.add_parser("observe"); so.add_argument("-n", type=int, default=10); so.add_argument("-i", type=float, default=0.5)
    sb = sub.add_parser("baseline"); sb.add_argument("-n", type=int, default=60); sb.add_argument("-i", type=float, default=0.5)
    sr = sub.add_parser("run")
    sr.add_argument("--type", default="telemetry_stall"); sr.add_argument("--target", default="fms-forwarder")
    sr.add_argument("--duration", type=int, required=True); sr.add_argument("--approved-by", required=True)
    sr.add_argument("--hypothesis"); sr.add_argument("--yes", action="store_true")
    sub.add_parser("stop")
    rs = sub.add_parser("reset"); rs.add_argument("--operator", required=True); rs.add_argument("--note", required=True)
    sub.add_parser("runs")
    sh = sub.add_parser("show"); sh.add_argument("run_id"); sh.add_argument("--json", action="store_true")
    sub.add_parser("deps")
    sbs = sub.add_parser("show-baseline"); sbs.add_argument("file", nargs="?")
    sv = sub.add_parser("serve"); sv.add_argument("--port", type=int, default=8765)
    rp_ = sub.add_parser("report"); rp_.add_argument("--run"); rp_.add_argument("--out", default="reports/day1/explainer.html")
    args = p.parse_args(argv)

    if args.cmd == "serve":  # the app performs startup recovery itself, exactly like other commands
        from .api import serve
        serve(args.port)
        return 0

    if args.cmd == "report":  # read-only: handled before any Docker / safety-controller object is created
        from pathlib import Path
        from .explainer import build_report
        out = Path(args.out)
        out = out if out.is_absolute() else REPO_ROOT / out
        print(f"wrote {build_report(REPO_ROOT, args.run, out)}")
        return 0

    cfg, catalog, docker, ctl, observer, store, runner = build(_printer)
    if args.cmd == "stop":  # first, before anything that reads possibly-corrupt state files
        out = ctl.emergency_stop("cli")
        print(f"emergency stop engaged ({out}); an active run will roll back at its next poll. Run `reset` afterwards.")
        return 0

    recovered = ctl.startup_recovery()
    if recovered:
        rec = store.load_run(recovered["marker"].get("run_id", ""))
        if rec is not None and rec["state"] not in ("COMPLETED", "ABORTED", "LATCHED_UNSAFE"):
            rec["timeline"].append({"at": utcnow().isoformat(), "event": "interrupted_process_recovered",
                                    "detail": recovered["outcome"], "state": "LATCHED_UNSAFE"})
            rec.update(state="LATCHED_UNSAFE", stop_reason="error")
            rec["rollback"].update(attempted=True, by="startup_recovery", detail=recovered["outcome"])
            store.save_run(rec)
        print(f"{C['r']}{C['b']}Interrupted run found and cleaned up: {recovered}. Controller latched; review then `reset`.{C['x']}")

    if args.cmd == "status":
        print(json.dumps(ctl.status(), indent=2))
        for t in sorted({t for s in catalog.values() for t in s.allowed_targets}):
            print(f"target {t}: {docker.inspect(t)}")
        return 0

    if args.cmd == "observe":
        tracker = FreshnessTracker(cfg.stale_threshold_s)
        t0 = time.monotonic()
        for i in range(args.n):
            o = observer.observe()
            a = tracker.update(o)
            print(_fmt_obs({"phase": "observe", "t_s": round(time.monotonic() - t0, 2), "observation": o.to_dict(),
                            "assessment": a.to_dict()}), flush=True)
            if i < args.n - 1:
                time.sleep(args.i)
        return 0

    if args.cmd == "baseline":
        print(f"Collecting {args.n} samples every {args.i}s from {cfg.rfms_base_url} ...")
        summary, observations = collect_baseline(observer, args.n, args.i, cfg.stale_threshold_s)
        summary["load_conditions"] = _load_conditions(docker, cfg, summary, args.i)
        name = utcnow().strftime("baseline-%Y%m%dT%H%M%SZ")
        path = store.save_baseline(name, {"summary": summary, "observations": [o.to_dict() for o in observations]})
        print(render_baseline(summary))
        print(f"saved {path}")
        return 0

    if args.cmd == "run":
        req = {"experiment_type": args.type, "target": args.target, "duration_s": args.duration,
               "approved_by": args.approved_by}
        if args.hypothesis:
            req["hypothesis"] = args.hypothesis
        try:
            ctl.validate(req)
        except SafetyError as exc:
            print(f"{C['r']}REJECTED: {exc}{C['x']}")
            return 2
        spec = catalog[args.type]
        print(f"{C['b']}Experiment plan{C['x']}\n  type: {args.type} ({spec.mechanism})\n  target: {args.target}\n"
              f"  duration: {args.duration}s (watchdog at +{cfg.watchdog_grace_s}s)\n  hypothesis: {args.hypothesis}\n"
              f"  expected symptoms: {list(spec.expected_symptoms)}\n  risks: {list(spec.risks)}\n"
              f"  stop anytime: Ctrl+C here, `python -m resiliencepilot stop`, or scripts/kill-switch.sh")
        if not args.yes and input(f"Approve as {args.approved_by}? [y/N] ").strip().lower() != "y":
            print("not approved; nothing executed")
            return 1
        signal.signal(signal.SIGINT, lambda *_: ctl.signal_stop())  # lock-free, I/O-free
        try:
            rec = runner.run(req)
        except SafetyError as exc:
            print(f"{C['r']}REFUSED: {exc}{C['x']}")
            return 2
        ok = rec["state"] == "COMPLETED"
        print(f"\n{C['b']}Result: {(C['g'] if ok else C['r'])}{rec['state']}{C['x']}  stop_reason={rec['stop_reason']}")
        print("\n".join(_result_lines(rec)))
        print(f"  evidence: {store.data_dir / 'runs' / (rec['run_id'] + '.json')}")
        print()
        print(render_impact_chart(rec))
        return 0 if ok else 3

    if args.cmd == "reset":
        try:
            print(json.dumps(ctl.reset(args.operator, args.note), indent=2))
        except SafetyError as exc:
            print(f"{C['r']}{exc}{C['x']}")
            return 2
        return 0

    if args.cmd == "runs":
        for r in store.list_runs():
            rec = store.load_run(r)
            print(f"{r}  {rec['state']:<15} stop={rec['stop_reason']}  recovery={rec['recovery']['verified']}")
        return 0

    if args.cmd == "show":
        rec = store.load_run(args.run_id)
        if rec is None:
            print("not found")
            return 1
        print(json.dumps(rec, indent=2) if args.json else render_impact_chart(rec))
        return 0

    if args.cmd == "deps":
        print(render_dependency_map(load_dependency_map(REPO_ROOT), docker))
        return 0

    if args.cmd == "show-baseline":
        files = sorted((store.data_dir / "baselines").glob("*.json"))
        path = args.file or (str(files[-1]) if files else None)
        if path is None:
            print("no baselines stored")
            return 1
        with open(path, encoding="utf-8") as f:
            print(render_baseline(json.load(f)["summary"]))
        print(f"{C['d']}source: {path}{C['x']}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
