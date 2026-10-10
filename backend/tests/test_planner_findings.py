"""Rule-based planner (planner.py) and findings report (findings.py): ranking rules, allowlist-only candidates, findings rules."""
from __future__ import annotations

import copy
import json

from resiliencepilot.config import REPO_ROOT
from resiliencepilot.findings import derive_findings, render_markdown
from resiliencepilot.planner import plan, render_text

from test_scoring import STALL, _poll, _rec

CATALOG = {
    "telemetry_stall": {"mechanism": "docker_pause", "allowed_targets": ["fms-forwarder"], "max_duration_s": 30,
                        "expected_symptoms": ["HTTP 200", "createdDateTime stops"]},
    "dependency_failure": {"mechanism": "docker_stop", "allowed_targets": ["influxDB"], "max_duration_s": 15,
                           "expected_symptoms": ["HYPOTHESIS: HTTP 500", "HYPOTHESIS: data gap"]},
}
DEP_MAP = {"nodes": {"fms-forwarder": {"side": "vehicle"}, "databroker": {"side": "vehicle"},
                     "influxDB": {"side": "backend"}, "fms-server": {"side": "backend"},
                     "fms-consumer": {"side": "backend"}, "grafana": {"side": "backend"}, "resiliencepilot": {"side": "host"}},
           "edges": [{"from": "fms-forwarder", "to": "databroker"}, {"from": "fms-consumer", "to": "influxDB"},
                     {"from": "fms-server", "to": "influxDB"}, {"from": "grafana", "to": "influxDB"},
                     {"from": "resiliencepilot", "to": "fms-forwarder"}]}


def _dep(run_id, **kw):
    r = _rec(STALL, run_id=run_id, **kw)
    r["request"].update(experiment_type="dependency_failure", target="influxDB")
    return r


def test_candidates_come_only_from_the_allowlist():
    entries = plan(CATALOG, DEP_MAP, [])
    assert {(e["experiment_type"], e["target"]) for e in entries} == {("telemetry_stall", "fms-forwarder"),
                                                                     ("dependency_failure", "influxDB")}
    assert all(e["requires_human_approval"] and "--approved-by <you>" in e["command"] for e in entries)
    assert [e["rank"] for e in entries] == [1, 2]


def test_never_run_shared_store_ranks_first_with_exact_factors():
    entries = {e["experiment_type"]: e for e in plan(CATALOG, DEP_MAP, [])}
    dep, stall = entries["dependency_failure"], entries["telemetry_stall"]
    # dependency_failure: never run (0.4), 2/2 hypotheses (0.3), 3 neighbours = max (0.5), shared-store stop (0.2)
    assert dep["information"] == 0.7 and dep["risk"] == round(0.5 + 0.2 * 1.0, 3)
    # host-side node (resiliencepilot) is not a neighbour: forwarder has only databroker
    assert dep["evidence"]["dependents"] == ["fms-consumer", "fms-server", "grafana"]
    assert stall["evidence"]["dependents"] == ["databroker"]
    assert stall["risk"] == round(0.5 * 1 / 3 + 0.2 * 0.3, 3)
    assert dep["rank"] == 1


def test_evidence_lowers_information_and_latches_raise_risk():
    clean = [_rec(STALL, run_id=f"20261010T18000{i}Z-a") for i in range(4)]
    before = {e["experiment_type"]: e for e in plan(CATALOG, DEP_MAP, [])}["telemetry_stall"]
    after = {e["experiment_type"]: e for e in plan(CATALOG, DEP_MAP, clean)}["telemetry_stall"]
    assert after["information"] < before["information"] and after["evidence"]["full_window_runs"] == 4
    latched = [_dep("20261010T180000Z-b", ttr=None, state="LATCHED_UNSAFE"), _dep("20261010T180001Z-c")]
    dep = {e["experiment_type"]: e for e in plan(CATALOG, DEP_MAP, latched)}["dependency_failure"]
    assert dep["evidence"]["latched"] == 1 and dep["evidence"]["unresolved"] == 1
    assert dep["risk"] == round(0.5 + 0.3 * 0.5 + 0.2, 3)


def test_suggested_duration_is_capped_by_catalog_and_safety_config():
    runs = [_dep("20261010T180000Z-a", duration=25)]
    entries = {e["experiment_type"]: e for e in plan(CATALOG, DEP_MAP, runs, max_duration_s=20)}
    assert entries["dependency_failure"]["suggested_duration_s"] == 15      # catalog max
    assert entries["telemetry_stall"]["suggested_duration_s"] == 12         # default when never run
    assert {e["experiment_type"]: e for e in plan(CATALOG, DEP_MAP, [], max_duration_s=10)}[
        "telemetry_stall"]["suggested_duration_s"] == 10                   # safety config max


def test_plan_is_deterministic_and_renders():
    runs = [_rec(STALL, run_id="20261010T180000Z-a")]
    assert plan(CATALOG, DEP_MAP, runs) == plan(copy.deepcopy(CATALOG), DEP_MAP, list(runs))
    text = render_text(plan(CATALOG, DEP_MAP, runs))
    assert text.startswith("Experiment plan (rule-based; nothing is executed")
    assert "$ rp run --type dependency_failure --target influxDB" in text


def _rules(runs):
    return {(f["rule"], f["experiment_type"]): [h["run"] for h in f["runs"]] for f in derive_findings(runs)}


def test_findings_rules_fire_on_their_evidence():
    silent_after = STALL + [_poll("recovery", 13 + i * 0.5, fresh=False) for i in range(10)]  # 11 polls ~5.5 s
    runs = [_rec(STALL, run_id="20261010T180000Z-quiet"),
            _rec(silent_after, run_id="20261010T180001Z-after", ttr=None, state="LATCHED_UNSAFE"),
            _dep("20261010T180002Z-slow", ttr=24.0, data_gap={"max_gap_s": 4.0, "median_interval_s": 0.5}),
            _rec(STALL, run_id="20261010T180003Z-blast", final={"clean": False, "changes": [{"container": "grafana"}]})]
    r = _rules(runs)
    assert r[("not_recovered", "telemetry_stall")] == ["20261010T180001Z-after"]
    assert r[("silent_after_rollback", "telemetry_stall")] == ["20261010T180001Z-after"]
    assert "20261010T180000Z-quiet" in r[("silent_during_fault", "telemetry_stall")]
    assert r[("slow_recovery", "dependency_failure")] == ["20261010T180002Z-slow"]
    assert r[("data_loss", "dependency_failure")] == ["20261010T180002Z-slow"]
    assert r[("blast_radius", "telemetry_stall")] == ["20261010T180003Z-blast"]
    order = [f["rule"] for f in derive_findings(runs)]
    assert order.index("not_recovered") < order.index("silent_during_fault") < order.index("blast_radius")


def test_silent_after_rollback_needs_more_than_the_stale_threshold():
    # 10 recovery polls x 0.5 s = 5.0 s: not more than the 5.0 s threshold -> a normal cold start, no finding
    at_threshold = [p for p in STALL if p["phase"] == "fault"] + [_poll("recovery", 11 + i * 0.5, fresh=False)
                                                                  for i in range(10)]
    assert ("silent_after_rollback", "telemetry_stall") not in _rules([_rec(at_threshold)])
    assert ("silent_after_rollback", "telemetry_stall") in _rules([_rec(at_threshold + [_poll("recovery", 16, fresh=False)])])


def test_unscorable_runs_produce_no_findings_but_are_listed_with_annotations():
    runs = [_rec(STALL, run_id="20261010T180000Z-x", rollback=False)]
    assert derive_findings(runs) == []
    md = render_markdown(runs, plan(CATALOG, DEP_MAP, runs), "reports/test", {"20261010T180000Z-x": "crash test"})
    assert "No rule fired." in md and "## Runs not scored" in md and "_(note: crash test)_" in md


def test_cli_plan_and_findings_are_read_only(tmp_path, monkeypatch, capsys):
    import resiliencepilot.__main__ as cli
    monkeypatch.setattr(cli, "build", lambda *_: (_ for _ in ()).throw(AssertionError("safety objects built")))
    (tmp_path / "20261010T180000Z-a.json").write_text(json.dumps(_rec(STALL, run_id="20261010T180000Z-a")), "utf-8")
    (tmp_path / "annotations.json").write_text(json.dumps({"_about": "x", "20261010T180000Z-a": "first try"}), "utf-8")
    assert cli.main(["plan", "--dir", str(tmp_path), "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)
    assert {e["experiment_type"] for e in entries} == set(json.loads(
        (REPO_ROOT / "config" / "experiments.json").read_text(encoding="utf-8")))
    out = tmp_path / "f.md"
    assert cli.main(["findings", "--dir", str(tmp_path), "--out", str(out)]) == 0
    md = out.read_text(encoding="utf-8")
    assert "_(note: first try)_" in md and "_about" not in md and "## Next experiments" in md
