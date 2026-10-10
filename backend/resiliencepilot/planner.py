"""Rule-based experiment planner (read-only, deterministic). Ranks allowlisted experiments; never runs anything.

priority = 0.6 x information + 0.4 x risk   ("most informative / riskiest first")

information (0-1) = 0.4 x 1/(1 + full-window runs)          fewer clean measurements -> more to learn
                  + 0.3 x untested-hypothesis share          expected symptoms still marked HYPOTHESIS:
                  + 0.3 x unresolved-outcome share           LATCHED_UNSAFE or partial runs / all runs
risk (0-1)        = 0.5 x dependents / max dependents        containers next to the target in the dependency map
                  + 0.3 x latch rate                         LATCHED_UNSAFE runs / all runs
                  + 0.2 x mechanism severity                 pause 0.3, stop of a publisher 0.7, stop of a shared store 1.0

Every candidate comes from config/experiments.json (the allowlist); the planner cannot propose anything else, and
every plan entry needs a human approver before `rp run` will accept it.
"""
from __future__ import annotations

import statistics

from .scoring import score_run

W_INFO, W_RISK = 0.6, 0.4
SEVERITY = {"docker_pause": 0.3, "docker_stop": 0.7}
SHARED_STORE_SEVERITY = 1.0
DEFAULT_DURATION_S = 12


def _dependents(dep_map: dict) -> dict[str, set[str]]:
    """Container neighbours of each node in the curated map (host-side nodes such as this tool excluded)."""
    host = {n for n, v in dep_map["nodes"].items() if v.get("side") == "host"}
    adj: dict[str, set[str]] = {n: set() for n in dep_map["nodes"] if n not in host}
    for e in dep_map["edges"]:
        a, b = e["from"], e["to"]
        if a in host or b in host:
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def _severity(mechanism: str, n_dependents: int) -> float:
    if mechanism == "docker_stop" and n_dependents >= 3:
        return SHARED_STORE_SEVERITY
    return SEVERITY.get(mechanism, 1.0)


def plan(catalog: dict, dep_map: dict, runs: list[dict], max_duration_s: int = 30) -> list[dict]:
    """`catalog` is the raw experiments.json dict; `runs` are stored run records. Returns entries, highest priority first."""
    adj = _dependents(dep_map)
    max_dep = max((len(v) for v in adj.values()), default=1) or 1
    scores = [score_run(r) for r in runs]
    entries = []
    for etype, spec in sorted(catalog.items()):
        for target in spec["allowed_targets"]:
            mine = [(r, s) for r, s in zip(runs, scores)
                    if r["request"]["experiment_type"] == etype and r["request"]["target"] == target]
            n = len(mine)
            full = [s for _, s in mine if s["scorable"] and not s["partial"]]
            latched = sum(1 for r, _ in mine if r["state"] == "LATCHED_UNSAFE")
            unresolved = sum(1 for r, s in mine if r["state"] == "LATCHED_UNSAFE" or (s["scorable"] and s["partial"]))
            symptoms = spec.get("expected_symptoms", [])
            untested = sum(1 for x in symptoms if x.startswith("HYPOTHESIS"))
            deps = sorted(adj.get(target, set()))
            sev = _severity(spec["mechanism"], len(deps))

            f_fresh = 1 / (1 + len(full))
            f_hyp = untested / len(symptoms) if symptoms else 0.0
            f_unres = unresolved / n if n else 0.0
            info = 0.4 * f_fresh + 0.3 * f_hyp + 0.3 * f_unres
            f_dep = len(deps) / max_dep
            f_latch = latched / n if n else 0.0
            risk = 0.5 * f_dep + 0.3 * f_latch + 0.2 * sev
            priority = W_INFO * info + W_RISK * risk

            used = sorted({r["request"]["duration_s"] for r, s in mine if s["scorable"] and not s["partial"]})
            duration = min(used[-1] if used else DEFAULT_DURATION_S, spec.get("max_duration_s", max_duration_s),
                           max_duration_s)
            rationale = [
                f"{len(full)} full-window run(s) so far (information {f_fresh:.2f})",
                f"{untested}/{len(symptoms)} expected symptoms still labelled HYPOTHESIS in config/experiments.json",
                f"{unresolved}/{n} past runs unresolved (latched or cut short)" if n else "never run",
                f"{len(deps)} neighbouring container(s) in the dependency map: {', '.join(deps) or 'none'}",
                f"latch rate {latched}/{n}" if n else "no latch history",
                f"mechanism {spec['mechanism']} (severity {sev:.1f})",
            ]
            if full:
                rationale.append(f"score so far: median {round(statistics.median(s['score'] for s in full), 1)} "
                                 f"(range {min(s['score'] for s in full)}-{max(s['score'] for s in full)})")
            entries.append({
                "experiment_type": etype, "target": target, "mechanism": spec["mechanism"],
                "priority": round(priority, 3), "information": round(info, 3), "risk": round(risk, 3),
                "evidence": {"runs": n, "full_window_runs": len(full), "latched": latched, "unresolved": unresolved,
                             "untested_hypotheses": untested, "dependents": deps},
                "suggested_duration_s": duration, "rationale": rationale, "requires_human_approval": True,
                "command": f"rp run --type {etype} --target {target} --duration {duration} --approved-by <you>",
            })
    entries.sort(key=lambda e: (-e["priority"], e["experiment_type"], e["target"]))
    for i, e in enumerate(entries, 1):
        e["rank"] = i
    return entries


def render_text(entries: list[dict]) -> str:
    lines = ["Experiment plan (rule-based; nothing is executed: each entry needs a human approver)"]
    for e in entries:
        lines.append(f"{e['rank']}. {e['experiment_type']} on {e['target']}  priority {e['priority']:.3f}  "
                     f"(information {e['information']:.3f}, risk {e['risk']:.3f})")
        lines += [f"     - {r}" for r in e["rationale"]]
        lines.append(f"     $ {e['command']}")
    return "\n".join(lines)
