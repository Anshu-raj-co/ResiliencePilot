"""Terminal renderings for operators and demos. Pure presentation: reads records, never changes state."""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ or os.environ.get("FORCE_COLOR") == "1"
C = {k: (v if _COLOR else "") for k, v in
     {"g": "\033[32m", "r": "\033[31m", "y": "\033[33m", "c": "\033[36m", "b": "\033[1m", "d": "\033[2m", "x": "\033[0m"}.items()}


def age_bar(age: float | None, threshold: float, width: int = 24) -> str:
    """Bar of data age; '|' marks the stale threshold. Scale: 0 .. 2.5x threshold."""
    if age is None:
        return " " * (width + 2)
    scale = threshold * 2.5
    filled = min(width, round(age / scale * width))
    mark = round(threshold / scale * width)
    cells = []
    for i in range(width):
        if i < filled:
            cells.append((C["r"] if i >= mark else C["g"]) + "█" + C["x"])
        else:
            cells.append(C["d"] + ("|" if i == mark else "·") + C["x"])
    return "[" + "".join(cells) + "]"


# ---------------- dependency map ----------------
def load_dependency_map(root: Path) -> dict:
    return json.loads((root / "config" / "dependency_map.json").read_text(encoding="utf-8"))


def render_dependency_map(dep: dict, docker=None) -> str:
    out = [f"{C['b']}Telemetry path (verified){C['x']}",
           "  " + f" {C['d']}→{C['x']} ".join(dep["telemetry_path"]),
           f"{C['b']}Edges{C['x']}  {C['d']}(confidence: source = code/config, experiment = fault upstream changed fms-server output, "
           f"live_network = shared Docker network right now){C['x']}"]
    for e in dep["edges"]:
        conf = list(e["confidence"])
        if docker is not None and e["from"] != "resiliencepilot":
            shared = set(docker.networks(e["from"])) & set(docker.networks(e["to"]))
            conf.append("live_network" if shared else f"{C['r']}NO shared network{C['x']}")
        path = f"{C['c']}*{C['x']}" if e["on_telemetry_path"] else " "
        out.append(f" {path} {e['from']:<16} → {e['to']:<16} {e['protocol'][:52]:<52} [{', '.join(conf)}]")
    out.append(f"  {C['c']}*{C['x']} = on the telemetry path")
    out.append(f"{C['b']}Timestamps{C['x']}")
    for k, v in dep["timestamps"].items():
        out.append(f"  {k:<17} {v}")
    out.append(f"{C['b']}Not present{C['x']}: " + "; ".join(dep["not_present"]))
    return "\n".join(out)


# ---------------- baseline ----------------
def render_baseline(summary: dict) -> str:
    a, iv, lat = summary["data_age_s"] or {}, summary["created_advance_interval_s"] or {}, summary["latency_ms"] or {}
    fc = summary["freshness_counts"]
    rows = [
        f"{C['b']}Baseline{C['x']}  {summary['samples']} samples every {summary.get('interval_s')}s "
        f"({summary['started_at'][11:19]}–{summary['ended_at'][11:19]} UTC)",
        f"  HTTP 200 ratio            {summary['http_ok_ratio']}",
        f"  FRESH / STALE / NO_DATA   {fc['FRESH']} / {fc['STALE']} / {fc['NO_DATA']}   (stale threshold {summary['stale_threshold_s']}s)",
        f"  data age (s)              min {a.get('min')}  median {a.get('median')}  p95 {a.get('p95')}  max {a.get('max')}",
        f"  createdDateTime advances  every {iv.get('median')}s (min {iv.get('min')}, max {iv.get('max')}); "
        f"regressions {summary['regressions']}",
        f"  rFMS API latency (ms)     median {lat.get('median')}  p95 {lat.get('p95')}  max {lat.get('max')}",
    ]
    lc = summary.get("load_conditions")
    if lc:
        rows.append(f"{C['b']}Load conditions{C['x']} (captured live with the baseline)")
        rows.append(f"  forwarder TIMER_INTERVAL={lc.get('forwarder_timer_interval')}  csv replay PROVIDER_INFINITE="
                    f"{lc.get('csv_provider_infinite')} ({lc.get('csv_rows')} rows)  vehicles={lc.get('vehicles')}")
        rows.append(f"  observer: 1 client polling every {lc.get('observer_poll_interval_s')}s; other clients: "
                    f"{lc.get('other_clients')}")
        rows.append(f"  Docker VM memory {lc.get('docker_mem_total_gib')} GiB; docker stats snapshot taken at the end of the baseline:")
        for s in lc.get("stack_stats", []):
            rows.append(f"    {s['name']:<17} cpu {s['cpu']:>6}  mem {s['mem']}")
    else:
        rows.append(f"  {C['y']}load conditions: not recorded for this baseline{C['x']}")
    return "\n".join(rows)


# ---------------- impact chart ----------------
def render_impact_chart(rec: dict) -> str:
    obs = rec["observations"]
    thr = rec["limits"]["stale_threshold_s"]
    ages = [o["assessment"]["data_age_s"] for o in obs]
    known = [a for a in ages if a is not None]
    if not known:
        return "no observations with data age"
    top = max(math.ceil(max(known)), math.ceil(thr) + 1)
    step = 1 if top <= 16 else math.ceil(top / 14)
    lines = [f"{C['b']}Data age at the rFMS API over the experiment{C['x']}  (one column per poll, every "
             f"{rec['limits']['poll_interval_s']}s; red = above the {thr}s stale threshold)"]
    for y_bot in range(top - step, -1, -step):  # each row covers [y_bot, y_bot+step)
        is_thr = y_bot <= thr < y_bot + step
        label = f"{y_bot:4d}s ┤" if (y_bot % (2 * step) == 0 or is_thr) else "      │"
        cells = []
        for a in ages:
            if a is not None and a >= y_bot + step / 2:
                cells.append((C["r"] if a > thr else C["g"]) + "█" + C["x"])
            elif is_thr:
                cells.append(C["y"] + "─" + C["x"])
            else:
                cells.append(" ")
        suffix = f" {C['y']}← stale threshold {thr}s{C['x']}" if is_thr else ""
        lines.append(label + "".join(cells) + suffix)
    lines.append("       └" + "─" * len(ages))
    http = "".join((C["g"] + "2" if o["observation"]["http_status"] == 200 else C["r"] + "x") + C["x"] for o in obs)
    lines.append("   HTTP  " + http + f"  {C['d']}(2 = HTTP 200 on that poll){C['x']}")
    phase_ch = {"precheck": C["d"] + "p", "fault": C["r"] + "F", "recovery": C["g"] + "R"}
    lines.append("  phase  " + "".join(phase_ch[o["phase"]] + C["x"] for o in obs)
                 + f"  {C['d']}(p = precheck, F = fault active, R = after rollback){C['x']}")

    tl = {e["event"]: e.get("t_s") for e in rec["timeline"]}
    inj = tl.get("fault_active")
    if inj is not None:
        def rel(ev):
            return None if tl.get(ev) is None else round(tl[ev] - inj, 2)
        imp, rc = rec["impact"], rec["recovery"]
        lines.append(f"{C['b']}Timeline (t=0 at verified injection){C['x']}")
        verified_by = next(((e.get("detail") or {}).get("verified_by") for e in rec["timeline"]
                            if e["event"] == "fault_active"), None)
        lines.append(f"  t={0:6.2f}s  fault active ({verified_by or 'verified'}) on {rec['request']['target']} "
                     f"[{rec['request']['experiment_type']}]")
        trip = (rec.get("guardrails") or {}).get("triggered")
        if trip:
            lines.append(f"  guardrail TRIPPED: {trip['guard']} at run t={trip['t_s']}s -> {rec['stop_reason']}")
        if imp.get("time_to_detection_s") is not None:
            lines.append(f"  t={imp['time_to_detection_s']:6.2f}s  STALE detected while HTTP 200  "
                         f"({imp['healthy_but_stale_observations']} healthy-but-stale polls, max age {imp['max_data_age_s']}s)")
        if rel("fault_window_closed") is not None:
            lines.append(f"  t={rel('fault_window_closed'):6.2f}s  fault window closed: {rec['stop_reason']}")
        if rel("rollback_result") is not None:
            lines.append(f"  t={rel('rollback_result'):6.2f}s  rollback verified={rec['rollback']['verified']} "
                         f"(by {rec['rollback']['by']})")
        if rc.get("time_to_first_fresh_s") is not None and rel("rollback_result") is not None:
            lines.append(f"  t={rel('rollback_result') + rc['time_to_first_fresh_s']:6.2f}s  first fresh vehicle data "
                         f"(+{rc['time_to_first_fresh_s']}s after rollback)")
        if rel("recovery_verified") is not None:
            lines.append(f"  t={rel('recovery_verified'):6.2f}s  recovery VERIFIED (+{rc['time_to_verified_s']}s after rollback)")
        lines.append(f"  final state: {rec['state']}")
    return "\n".join(lines)
