"""Dashboard API: every POST goes through the existing safety controller; a run takes only a catalogue id and a typed approver."""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from resiliencepilot import api as api_mod
from resiliencepilot.api import DASHBOARD_DURATION_S, HOST, create_app, dashboard_request
from resiliencepilot.config import ExperimentSpec
from resiliencepilot.evidence import EvidenceStore
from resiliencepilot.safety import SafetyController

from helpers import FakeDocker, FakeObserver, FakeWorld, make_config

CATALOG = {"telemetry_stall": ExperimentSpec("telemetry_stall", "docker_pause", ("fms-forwarder",), 30)}


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path)
    world = FakeWorld()
    docker = FakeDocker(world)
    ctl = SafetyController(cfg, CATALOG, docker, observer=FakeObserver(world))
    store = EvidenceStore(cfg.data_dir)
    app = create_app(cfg, CATALOG, ctl, docker, FakeObserver(world), store, dashboard_duration_s=2, monitor=True)
    with TestClient(app) as a, TestClient(app) as b:  # two independent clients on the same server
        yield {"cfg": cfg, "world": world, "ctl": ctl, "store": store, "app": app, "a": a, "b": b}


def wait_idle(e, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if e["a"].get("/api/status").json()["state"] != "BUSY":
            return
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def wait_paused(e):
    for _ in range(200):
        if e["world"].forwarder_paused():
            return
        time.sleep(0.02)
    raise AssertionError("fault never injected")


def test_server_binds_localhost_only():
    assert HOST == "127.0.0.1"


PICK = {"experiment_id": "telemetry_stall", "approved_by": "Jane Doe"}


def test_request_comes_from_the_catalogue_not_the_client():
    spec = ExperimentSpec("dependency_failure", "docker_stop", ("influxDB",), 15, expected_symptoms=("HTTP 500",))
    req = dashboard_request("dependency_failure", "Jane Doe", {"dependency_failure": spec}, 30)
    assert req["target"] == "influxDB" and req["duration_s"] == min(DASHBOARD_DURATION_S, 15)
    assert req["approved_by"] == "Jane Doe" and "HTTP 500" in req["hypothesis"]


@pytest.mark.parametrize("kwargs", [
    {},                                                                        # no body
    {"json": {"duration_s": 30}},
    {"json": {**PICK, "target": "influxDB"}},                                  # any extra field is refused
    {"json": {**PICK, "duration_s": 30}},
    {"json": {"experiment_id": "rm -rf", "approved_by": "Jane"}},             # not a catalogue id
    {"json": {"experiment_id": "dependency_failure", "approved_by": "Jane"}},  # valid type, not in this catalogue
    {"json": {"experiment_id": "telemetry_stall", "approved_by": "   "}},     # blank approver
    {"json": {"experiment_id": "telemetry_stall", "approved_by": "123"}},     # not a name
    {"json": {"experiment_id": "telemetry_stall"}},                           # no approver
    {"json": {"experiment_id": ["telemetry_stall"], "approved_by": "Jane"}},  # wrong type
    {"json": {**PICK, "profile": "guardrail-demo-latency"}},                  # no way to pick a config profile
    {"json": {"experiment_id": "guardrail-demo-latency", "approved_by": "Jane"}},
    {"json": PICK, "params": {"target": "influxDB"}},
    {"content": b"experiment_id=telemetry_stall"},
])
def test_run_accepts_only_a_catalogue_id_and_a_typed_name(env, kwargs):
    r = env["a"].post("/api/run", **kwargs)
    assert r.status_code == 422
    assert env["world"].calls == []


def test_run_records_the_typed_approver_and_what_they_saw(env):
    r = env["a"].post("/api/run", json={"experiment_id": "telemetry_stall", "approved_by": "  Jane   Doe "})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    wait_idle(env)
    rec = env["store"].load_run(run_id)
    assert rec["request"]["approved_by"] == "Jane Doe" and rec["request"]["target"] == "fms-forwarder"
    assert rec["request"]["duration_s"] == 2
    assert rec["state"] == "COMPLETED" and rec["recovery"]["verified"]
    prop = env["store"].load_proposal(run_id)
    assert prop["approved_by"] == "Jane Doe" and prop["approved_via"] == "dashboard"
    assert prop["proposal"]["experiment_id"] == "telemetry_stall" and prop["proposal"]["rank"] == 1
    assert prop["proposal"]["provider"] == "rule-based" and prop["model_calls"] == []
    s = env["a"].get("/api/status").json()
    assert s["last_run"]["run_id"] == run_id and s["phase"] == "recovered" and s["last_run"]["approved_by"] == "Jane Doe"
    assert env["a"].get("/api/runs/latest").json()["run_id"] == run_id


def test_proposals_are_the_planner_ranking_with_reasons_and_predictions(env):
    d = env["a"].get("/api/proposals").json()
    assert d["catalog"] == ["telemetry_stall"]
    (p,) = d["proposals"]
    assert p["experiment_id"] == "telemetry_stall" and p["rank"] == 1 and p["rationale"]
    assert p["target"] == "fms-forwarder" and p["duration_s"] == 2
    assert {"predicted_symptoms", "risks", "priority", "provider"} <= set(p)


def test_busy_returns_409(env):
    assert env["a"].post("/api/run", json=PICK).status_code == 202
    assert env["b"].post("/api/run", json=PICK).status_code == 409
    wait_idle(env)
    assert len([c for c in env["world"].calls if c[1] == "pause"]) == 1


def test_busy_in_another_process_returns_409(env):
    (env["cfg"].data_dir / "run.lock").write_text(json.dumps({"pid": os.getppid(), "run_id": "cli-run"}))
    assert env["a"].post("/api/run", json=PICK).status_code == 409
    assert env["world"].calls == []


def test_latched_returns_423_and_reset_needs_confirm(env):
    env["ctl"].latch("test latch")
    assert env["a"].post("/api/run", json=PICK).status_code == 423
    assert env["a"].post("/api/reset").status_code == 400
    assert env["a"].post("/api/reset", json={"confirm": "yes"}).status_code == 400
    assert env["a"].post("/api/reset", json={"confirm": True}).status_code == 200
    assert env["a"].get("/api/status").json()["state"] == "READY"


def test_reset_refused_when_not_latched(env):
    assert env["a"].post("/api/reset", json={"confirm": True}).status_code == 409


def test_kill_from_second_client_rolls_back_and_blocks(env):
    run_id = env["a"].post("/api/run", json=PICK).json()["run_id"]
    wait_paused(env)
    assert env["b"].post("/api/kill").status_code == 200       # a different client stops it
    wait_idle(env)
    rec = env["store"].load_run(run_id)
    assert rec["stop_reason"] == "emergency_stop"
    assert rec["rollback"]["verified"] and rec["recovery"]["verified"]
    assert not env["world"].forwarder_paused()
    assert env["a"].post("/api/run", json=PICK).status_code == 423           # blocked until reset
    assert env["b"].post("/api/reset", json={"confirm": True}).status_code == 200
    assert env["a"].post("/api/run", json=PICK).status_code == 202
    wait_idle(env)


def test_status_reports_live_values_from_observer(env):
    time.sleep(0.3)
    s = env["a"].get("/api/status").json()
    assert s["latest"]["http_status"] == 200 and s["latest"]["data_age_s"] is not None
    assert s["stale_threshold_s"] == env["cfg"].stale_threshold_s
    assert env["a"].get("/").status_code == 200 and "<title>ResiliencePilot Live</title>" in env["a"].get("/").text


def test_api_module_has_no_safety_logic_of_its_own():
    src = open(api_mod.__file__, encoding="utf-8").read()
    for forbidden in ("docker.pause", "docker.unpause", ".pause(", ".unpause(", "mark_fault_active", "clear_fault_marker"):
        assert forbidden not in src


def test_last_run_never_shows_an_in_progress_record(env):
    first = env["a"].post("/api/run", json=PICK).json()["run_id"]
    wait_idle(env)
    env["a"].post("/api/run", json=PICK)
    wait_paused(env)                                   # second run is active now
    s = env["a"].get("/api/status").json()
    assert s["state"] == "BUSY"
    assert s["last_run"]["run_id"] == first and s["last_run"]["state"] == "COMPLETED"
    wait_idle(env)


# ---- Phase E: read-only insights (scores, planner proposals, findings) ----
@pytest.mark.parametrize("src", ["../../etc", "/tmp", "reports/day2", "LOCAL", ""])
def test_insights_source_is_a_fixed_name_never_a_path(env, src):
    r = env["a"].get("/api/insights", params={"source": src})
    assert r.status_code == 422 and "local" in r.json()["detail"]


def test_insights_on_committed_evidence_uses_the_servers_allowlist(env):
    d = env["a"].get("/api/insights", params={"source": "day2"}).json()
    assert d["source"] == "day2" and d["runs"] >= 20
    # the planner only sees the catalog this server enforces (here: telemetry_stall on fms-forwarder only)
    assert [(e["experiment_type"], e["target"]) for e in d["plan"]] == [("telemetry_stall", "fms-forwarder")]
    assert all(e["requires_human_approval"] for e in d["plan"])
    assert {"dependency_failure", "service_crash", "telemetry_stall"} <= set(d["summary"])
    notes = [h.get("note") for f in d["findings"] for h in f["runs"] if h.get("note")]
    assert any("deliberate guard test" in n for n in notes) and all("title" in f for f in d["findings"])


def test_insights_local_scores_a_dashboard_run(env):
    empty = env["a"].get("/api/insights").json()
    assert empty["runs"] == 0 and empty["latest_scored"] is None and empty["findings"] == []
    assert env["a"].post("/api/run", json=PICK).status_code == 202
    wait_idle(env)
    d = env["a"].get("/api/insights").json()
    assert d["runs"] == 1 and d["recent"][0]["run_id"] == env["store"].list_runs()[-1]
    s = d["latest_scored"]
    assert s is not None and 0 <= s["score"] <= 100 and set(s["components"]) == {
        "detection", "recovery", "data_integrity", "blast_radius"}


def test_insights_failure_never_breaks_the_controls(env, monkeypatch):
    import resiliencepilot.insights as ins
    monkeypatch.setattr(ins, "build_insights", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = env["a"].get("/api/insights")
    assert r.status_code == 500 and "boom" in r.json()["detail"]
    assert env["a"].get("/api/status").status_code == 200


def test_page_picks_one_catalogue_id_and_a_typed_name():
    import re
    html = (api_mod._STATIC).read_text(encoding="utf-8")
    for el in ('id="score"', 'id="summary"', 'id="recent"', 'id="plan"', 'id="findings"', 'id="pick"', 'id="approver"',
               "/api/proposals", "/api/insights?source="):
        assert el in html
    # the only POSTs remain the three controls; the run body carries exactly the id and the typed name
    assert sorted(set(re.findall(r'post\("(/api/[a-z]+)"', html))) == ["/api/kill", "/api/reset", "/api/run"]
    assert 'post("/api/run",{experiment_id:p.experiment_id,approved_by:name},"Start")' in html
    assert "dashboard-operator" not in html
    assert '<span class="cmd">' in html                      # the insights planner card stays text only


def test_proposals_failure_never_blocks_a_run(env, monkeypatch):
    import resiliencepilot.planner as pl
    monkeypatch.setattr(pl, "plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert env["a"].get("/api/proposals").status_code == 500
    r = env["a"].post("/api/run", json=PICK)
    assert r.status_code == 202
    wait_idle(env)
    assert env["store"].load_proposal(r.json()["run_id"])["proposal"] is None


def test_dashboard_refuses_to_run_with_a_config_profile(tmp_path):
    import dataclasses
    cfg = dataclasses.replace(make_config(tmp_path), profile="guardrail-demo-latency")
    world = FakeWorld()
    docker = FakeDocker(world)
    with pytest.raises(ValueError, match="RP_CONFIG_PROFILE"):
        create_app(cfg, CATALOG, SafetyController(cfg, CATALOG, docker, observer=FakeObserver(world)), docker,
                   FakeObserver(world), EvidenceStore(cfg.data_dir), monitor=False)


def test_serve_with_a_profile_set_fails_before_binding(monkeypatch):
    monkeypatch.setenv("RP_CONFIG_PROFILE", "guardrail-demo-error-rate")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("server started")))
    monkeypatch.setattr("resiliencepilot.docker_cli.DockerCLI", lambda: FakeDocker(FakeWorld()))
    with pytest.raises(ValueError, match="RP_CONFIG_PROFILE"):
        api_mod.serve()
