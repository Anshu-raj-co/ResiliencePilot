"""Operator CLI.  python -m resiliencepilot <command>

  status                     safety controller state + target containers
  observe [-n N]             poll the rFMS API and show freshness (read-only)
  baseline [-n N] [-i S]     collect and save a baseline
  run --duration S --approved-by NAME [--hypothesis TEXT] [--yes]
                             run the telemetry_stall experiment (asks for approval unless --yes)
  stop                       emergency stop (works from another terminal)
  reset --operator NAME --note TEXT
  runs | show RUN_ID
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time

from .baseline import collect_baseline
from .config import load_experiment_catalog, load_safety_config
from .docker_cli import DockerCLI
from .evidence import EvidenceStore
from .observation import utcnow
from .orchestrator import ExperimentRunner
from .safety import SafetyController, SafetyError
from .sdv_adapter import RfmsObserver
from .telemetry import FreshnessTracker

C = {"g": "\033[32m", "r": "\033[31m", "y": "\033[33m", "b": "\033[1m", "d": "\033[2m", "x": "\033[0m"}


def _fmt_obs(entry: dict) -> str:
    o, a = entry["observation"], entry["assessment"]
    color = {"FRESH": C["g"], "STALE": C["r"]}.get(a["freshness"], C["y"])
    http = o["http_status"] if o["http_status"] is not None else "ERR"
    created = (o["created_at"] or "-")[11:23]
    received = (o["received_at"] or "-")[11:23]
    flag = f"  {C['r']}{C['b']}<- HTTP 200 but STALE{C['x']}" if a["healthy_but_stale"] else ""
    return (f"  [{entry['phase']:>8} t={entry['t_s']:6.2f}s] HTTP {http}  createdDateTime={created}  "
            f"receivedDateTime={received}  age={a['data_age_s']}s  {color}{a['freshness']}{C['x']}{flag}")


def _printer(kind: str, data: dict) -> None:
    if kind == "observation":
        print(_fmt_obs(data), flush=True)
    elif kind == "event":
        detail = "" if data["detail"] in (None, {}) else f"  {C['d']}{json.dumps(data['detail'], default=str)}{C['x']}"
        print(f"{C['b']}» {data['event']}{C['x']}  [{data['state']}]{detail}", flush=True)


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
    sh = sub.add_parser("show"); sh.add_argument("run_id")
    args = p.parse_args(argv)

    cfg, catalog, docker, ctl, observer, store, runner = build(_printer)
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
        name = utcnow().strftime("baseline-%Y%m%dT%H%M%SZ")
        path = store.save_baseline(name, {"summary": summary, "observations": [o.to_dict() for o in observations]})
        print(json.dumps(summary, indent=2))
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
        signal.signal(signal.SIGINT, lambda *_: ctl.emergency_stop("ctrl-c"))
        try:
            rec = runner.run(req)
        except SafetyError as exc:
            print(f"{C['r']}REFUSED: {exc}{C['x']}")
            return 2
        imp, rb, rc = rec["impact"], rec["rollback"], rec["recovery"]
        ok = rec["state"] == "COMPLETED"
        print(f"\n{C['b']}Result: {(C['g'] if ok else C['r'])}{rec['state']}{C['x']}  stop_reason={rec['stop_reason']}")
        print(f"  fault verified: {imp.get('fault_verified')}   stale detected: {imp.get('stale_detected')} "
              f"(time to detection {imp.get('time_to_detection_s')}s)")
        print(f"  during fault: HTTP-200 ratio {imp.get('http_ok_ratio_during_fault')}, max data age {imp.get('max_data_age_s')}s, "
              f"{imp.get('healthy_but_stale_observations')} healthy-but-stale observations")
        print(f"  rollback: verified={rb['verified']} by={rb['by']}   recovery: verified={rc['verified']} "
              f"first fresh after {rc['time_to_first_fresh_s']}s, verified after {rc['time_to_verified_s']}s")
        print(f"  evidence: {store.data_dir / 'runs' / (rec['run_id'] + '.json')}")
        return 0 if ok else 3

    if args.cmd == "stop":
        out = ctl.emergency_stop("cli")
        print(f"emergency stop engaged ({out}); an active run will roll back at its next poll. Run `reset` afterwards.")
        return 0

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
        print(json.dumps(rec, indent=2) if rec else "not found")
        return 0 if rec else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
