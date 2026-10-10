"""Dashboard insights (read-only): scores, planner proposals and findings for one run-record directory.

Built from the same functions as `rp score`, `rp plan` and `rp findings`. Takes the catalog the server already
enforces (ExperimentSpec objects), so the planner can only propose what the safety controller allows.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import ExperimentSpec
from .findings import _TITLE, derive_findings
from .planner import plan
from .scoring import load_runs, score_run, summarise

RECENT_RUNS = 10


def catalog_dict(catalog: dict[str, ExperimentSpec]) -> dict:
    return {k: {"mechanism": s.mechanism, "allowed_targets": list(s.allowed_targets), "max_duration_s": s.max_duration_s,
                "expected_symptoms": list(s.expected_symptoms)} for k, s in catalog.items()}


def _annotations(directory: Path) -> dict:
    f = directory / "annotations.json"
    try:
        raw = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (OSError, ValueError):
        return {}
    return {k: str(v) for k, v in raw.items() if not k.startswith("_")}


def build_insights(directory: Path, catalog: dict[str, ExperimentSpec], dep_map: dict, max_duration_s: int) -> dict:
    runs = load_runs(directory) if directory.is_dir() else []
    notes = _annotations(directory)
    scores = [score_run(r) for r in runs]
    latest = next((s for s in reversed(scores) if s["scorable"]), None)
    findings = derive_findings(runs)
    for f in findings:
        f["title"] = _TITLE[f["rule"]]
        for h in f["runs"]:
            if h["run"] in notes:
                h["note"] = notes[h["run"]]
    recent = []
    for s in reversed(scores[-RECENT_RUNS:]):
        row = {k: s[k] for k in ("run_id", "experiment_type", "state", "stop_reason", "scorable")}
        row.update({"score": s.get("score"), "partial": s.get("partial"), "reason": s.get("reason"),
                    "note": notes.get(s["run_id"])})
        recent.append(row)
    return {"runs": len(runs), "latest_scored": latest, "summary": summarise(scores), "recent": recent,
            "plan": plan(catalog_dict(catalog), dep_map, runs, max_duration_s), "findings": findings}
