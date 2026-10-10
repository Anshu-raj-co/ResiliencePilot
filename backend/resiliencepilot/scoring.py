"""Resilience Score (read-only): rates how the SDV stack behaved under one verified fault. Formula: docs/SCORING.md.

Every input is read from a stored run record (observations, timeline, rollback/recovery, data_gap, guardrails).
A component that the record does not measure is reported as not measured and left out of the weighted mean;
nothing is estimated or filled in.
"""
from __future__ import annotations

import statistics
from datetime import datetime
from pathlib import Path

WEIGHTS = {"detection": 0.25, "recovery": 0.30, "data_integrity": 0.20, "blast_radius": 0.25}
SILENT_PENALTY = 0.5            # lambda: a run that is silent on every poll keeps at most half its score
NOT_SCORABLE_STOPS = {"emergency_stop", "cancelled", "error"}   # stopped by an operator or by the tool itself


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _event_at(rec: dict, event: str) -> datetime | None:
    return next((_ts(e["at"]) for e in rec.get("timeline", []) if e["event"] == event), None)


def _not_scorable(rec: dict) -> str | None:
    if not rec.get("impact", {}).get("fault_verified"):
        return "fault was never verified active"
    if not rec.get("rollback", {}).get("verified"):
        return "rollback not verified (system state unknown)"
    if rec.get("stop_reason") in NOT_SCORABLE_STOPS:
        return f"stopped by {rec['stop_reason']}, not by the system's response"
    if _event_at(rec, "fault_active") is None:
        return "no fault_active event in the timeline"
    return None


def detection(rec: dict) -> dict:
    """First fault-window poll a client could act on: no/non-200 response (visible) or HTTP 200 with STALE data (silent)."""
    t0 = _event_at(rec, "fault_active")
    duration = float(rec["request"]["duration_s"])
    for entry in rec.get("observations", []):
        if entry["phase"] != "fault":
            continue
        o, a = entry["observation"], entry["assessment"]
        visible = o.get("http_status") != 200
        if visible or a.get("freshness") == "STALE":
            ttd = max(0.0, (_ts(o["observed_at"]) - t0).total_seconds())
            return {"measured": True, "ttd_s": round(ttd, 3), "kind": "visible" if visible else "silent",
                    "score": round(_clamp(1 - ttd / duration), 3)}
    return {"measured": True, "ttd_s": None, "kind": "not detected during the fault window", "score": 0.0}


def recovery(rec: dict) -> dict:
    rc, timeout = rec["recovery"], float(rec.get("limits", {}).get("recovery_timeout_s", 30.0))
    if not rc.get("verified") or rc.get("time_to_verified_s") is None:
        return {"measured": True, "ttr_s": None, "score": 0.0,
                "note": f"not recovered within {timeout:g} s ({rc.get('detail')})"}
    ttr = float(rc["time_to_verified_s"])
    return {"measured": True, "ttr_s": ttr, "score": round(_clamp(1 - ttr / timeout), 3)}


def data_integrity(rec: dict) -> dict:
    gap = rec.get("data_gap")
    if not gap or gap.get("max_gap_s") is None or gap.get("median_interval_s") is None:
        return {"measured": False, "note": "no stored-data-gap evidence in this record"}
    lost = max(0.0, float(gap["max_gap_s"]) - float(gap["median_interval_s"]))
    return {"measured": True, "lost_s": round(lost, 3),
            "score": round(_clamp(1 - lost / float(rec["request"]["duration_s"])), 3)}


def blast_radius(rec: dict) -> dict:
    g = rec.get("guardrails") or {}
    baseline, allowed = g.get("blast_radius_baseline"), set(g.get("blast_radius_allowed") or [])
    final, trig = g.get("final_blast_check"), g.get("triggered")
    if not baseline or (final is None and not (trig and trig.get("guard") == "blast_radius")):
        return {"measured": False, "note": "no blast-radius check recorded"}
    changed = {c["container"] for c in (final or {}).get("changes", [])}
    if trig and trig.get("guard") == "blast_radius":
        changed |= {c["container"] for c in trig["detail"].get("changes", [])}
    others = [c for c in baseline if c not in allowed]
    return {"measured": True, "changed": sorted(changed), "others": len(others),
            "score": round(_clamp(1 - len(changed) / len(others)), 3) if others else 1.0}


def silent_ratio(rec: dict) -> dict:
    """Share of fault + recovery polls that answered HTTP 200 with STALE data (the failure a health check misses)."""
    polls = [e for e in rec.get("observations", []) if e["phase"] in ("fault", "recovery")]
    silent = sum(1 for e in polls if e["assessment"].get("healthy_but_stale"))
    by_phase = {p: sum(1 for e in polls if e["phase"] == p and e["assessment"].get("healthy_but_stale"))
                for p in ("fault", "recovery")}
    return {"polls": len(polls), "silent_polls": silent, "by_phase": by_phase,
            "ratio": round(silent / len(polls), 3) if polls else 0.0}


def score_run(rec: dict) -> dict:
    base = {"run_id": rec["run_id"], "experiment_type": rec["request"]["experiment_type"],
            "target": rec["request"]["target"], "state": rec["state"], "stop_reason": rec["stop_reason"]}
    reason = _not_scorable(rec)
    if reason:
        return {**base, "scorable": False, "reason": reason}
    comps = {"detection": detection(rec), "recovery": recovery(rec),
             "data_integrity": data_integrity(rec), "blast_radius": blast_radius(rec)}
    measured = {k: c for k, c in comps.items() if c["measured"]}
    mean = sum(WEIGHTS[k] * c["score"] for k, c in measured.items()) / sum(WEIGHTS[k] for k in measured)
    silent = silent_ratio(rec)
    value = 100 * mean * (1 - SILENT_PENALTY * silent["ratio"])
    # only a fault held for the whole requested window is comparable across runs; a window cut short by the system's
    # own response (observer_lost, a guardrail) is still scored, but flagged partial and kept out of the summary
    partial = rec["stop_reason"] != "duration_elapsed"
    return {**base, "scorable": True, "partial": partial, "score": round(value, 1), "weighted_mean": round(mean, 3),
            "components": comps, "silent": silent, "not_measured": sorted(set(comps) - set(measured))}


def summarise(scores: list[dict]) -> dict:
    by_type: dict[str, list[float]] = {}
    for s in scores:
        if s["scorable"] and not s["partial"]:
            by_type.setdefault(s["experiment_type"], []).append(s["score"])
    return {t: {"n": len(v), "median": round(statistics.median(v), 1), "min": min(v), "max": max(v)}
            for t, v in sorted(by_type.items())}


def load_runs(directory: Path) -> list[dict]:
    import json
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))
            if p.name[:8].isdigit()]


def render_text(scores: list[dict]) -> str:
    lines = [f"{'run':<26} {'type':<19} {'state':<15} {'score':>5}  D     R     G     B     silent"]
    fmt = lambda c: f"{c['score']:.2f}" if c["measured"] else " n/m"  # noqa: E731
    for s in scores:
        if not s["scorable"]:
            lines.append(f"{s['run_id']:<26} {s['experiment_type']:<19} {s['state']:<15}     -  not scorable: {s['reason']}")
            continue
        c = s["components"]
        lines.append(f"{s['run_id']:<26} {s['experiment_type']:<19} {s['state']:<15} {s['score']:5.1f}  "
                     f"{fmt(c['detection'])}  {fmt(c['recovery'])}  {fmt(c['data_integrity'])}  {fmt(c['blast_radius'])}  "
                     f"{s['silent']['silent_polls']}/{s['silent']['polls']}"
                     + (f"  partial (window cut short: {s['stop_reason']})" if s["partial"] else ""))
    lines.append("D detection, R recovery, G data integrity, B blast radius (0-1; n/m = not measured); "
                 "silent = HTTP 200 but stale polls / fault+recovery polls. Formula: docs/SCORING.md")
    lines.append("Summary over full-window runs only (partial runs excluded):")
    for t, v in summarise(scores).items():
        lines.append(f"  {t:<19} n={v['n']}  median {v['median']}  range {v['min']}-{v['max']}")
    return "\n".join(lines)
