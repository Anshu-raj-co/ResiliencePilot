"""dependency_failure: stop/start influxDB with the expected-status rule.

Rule (approved by the lead):
  * the declared expected status (500) is tolerated during the FAULT WINDOW ONLY;
  * any other status, a timeout or a lost connection aborts immediately;
  * precheck and recovery stay strictly HTTP 200 + fresh advancing data.
"""
import time

import pytest

from resiliencepilot.config import ExperimentSpec, load_experiment_catalog
from resiliencepilot.evidence import EvidenceStore
from resiliencepilot.guardrails import ErrorRateGuard
from resiliencepilot.orchestrator import ExperimentRunner
from resiliencepilot.safety import RequestRejected, SafetyController

from helpers import FakeDocker, FakeObserver, FakeWorld, make_config

CATALOG = {
    "telemetry_stall": ExperimentSpec("telemetry_stall", "docker_pause", ("fms-forwarder",), 30),
    "service_crash": ExperimentSpec("service_crash", "docker_stop", ("fms-forwarder",), 15),
    "dependency_failure": ExperimentSpec("dependency_failure", "docker_stop", ("influxDB",), 15, expected_statuses=(500,)),
}
DEP = {"experiment_type": "dependency_failure", "target": "influxDB", "duration_s": 1, "approved_by": "tester"}


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path)
    world = FakeWorld()
    docker = FakeDocker(world)
    ctl = SafetyController(cfg, CATALOG, docker, observer=FakeObserver(world))
    runner = ExperimentRunner(cfg, CATALOG, ctl, docker, FakeObserver(world), EvidenceStore(cfg.data_dir))
    return cfg, world, docker, ctl, runner


def calls(world, kind):
    return [c for c in world.calls if c[1] == kind]


# ---------------- the rule, as pure logic ----------------
def test_expected_status_is_not_an_error_but_others_are():
    g = ErrorRateGuard(0.0, expected=(500,))
    assert [g.update(500) for _ in range(5)] == [None] * 5 and g.expected_seen == 5 and g.errors == 0
    assert g.update(503).guard == "error_rate"


def test_expected_statuses_must_be_http_error_codes():
    with pytest.raises(ValueError):
        ExperimentSpec("x", "docker_stop", ("influxDB",), 10, expected_statuses=(200,))
    with pytest.raises(ValueError):
        ExperimentSpec("x", "docker_stop", ("influxDB",), 10, expected_statuses=(302,))


def test_catalogue_declares_500_only_for_dependency_failure():
    cat = load_experiment_catalog()
    assert cat["dependency_failure"].expected_statuses == (500,)
    assert cat["dependency_failure"].allowed_targets == ("influxDB",) and cat["dependency_failure"].max_duration_s == 15
    assert cat["telemetry_stall"].expected_statuses == () and cat["service_crash"].expected_statuses == ()


# ---------------- validation ----------------
@pytest.mark.parametrize("bad", [{**DEP, "target": "fms-forwarder"}, {**DEP, "target": "fms-server"},
                                 {**DEP, "duration_s": 16}, {**DEP, "expected_statuses": [503]}])
def test_invalid_requests_rejected(env, bad):
    _, world, _, _, runner = env
    with pytest.raises(RequestRejected):
        runner.run(bad)
    assert world.calls == []


# ---------------- end to end ----------------
def test_success_expected_500_tolerated_then_strict_recovery(env):
    _, world, _, ctl, runner = env
    influx_id = world.containers["influxDB"].id
    rec = runner.run(DEP)
    imp = rec["impact"]
    assert rec["state"] == "COMPLETED" and rec["stop_reason"] == "duration_elapsed"
    assert imp["fault_verified"] and imp["expected_status_observations"] > 0
    assert imp["time_to_first_expected_status_s"] is not None
    assert imp["http_ok_ratio_during_fault"] == 0.0
    assert imp["silent_failure"] is False, "an HTTP 500 is visible to a plain health check"
    assert rec["guardrails"]["triggered"] is None and rec["guardrails"]["expected_statuses"] == [500]
    assert rec["rollback"]["verified"] and rec["recovery"]["verified"]
    assert world.containers["influxDB"].running and world.containers["influxDB"].id == influx_id
    assert len(calls(world, "stop")) == 1 and calls(world, "stop")[0][2] == "influxDB"
    assert any(e["event"] == "expected_symptom" for e in rec["timeline"])
    assert ctl.status()["state"] == "READY"


def test_data_gap_is_measured_after_recovery(env):
    _, world, _, _, runner = env
    rec = runner.run({**DEP, "duration_s": 2})
    gap = rec["data_gap"]
    assert gap["stored_statuses"] > 0
    assert gap["max_gap_s"] >= 1.5, "statuses produced while InfluxDB was down were never stored"
    assert gap["gap_from"] < gap["gap_to"]


def test_unexpected_status_during_fault_aborts_immediately(env):
    _, world, _, _, runner = env
    world.influx_down_status = 503
    rec = runner.run({**DEP, "duration_s": 5})
    assert rec["stop_reason"] == "guardrail_error_rate"
    assert rec["guardrails"]["triggered"]["detail"]["last_status"] == 503
    assert rec["rollback"]["verified"] and world.containers["influxDB"].running


def test_lost_connection_during_fault_aborts_on_first_poll(env):
    _, world, _, _, runner = env
    world.influx_down_status = None                   # API stops answering at all
    rec = runner.run({**DEP, "duration_s": 5})
    assert rec["stop_reason"] == "observer_lost"
    lost = [o for o in rec["observations"] if o["phase"] == "fault" and o["observation"]["http_status"] is None]
    assert len(lost) == 1, "with a declared expected status, the first lost poll aborts"
    assert rec["rollback"]["verified"]


def test_expected_status_is_not_tolerated_during_precheck(env):
    _, world, docker, _, runner = env
    world.api_status = 500                            # broken before we start: never inject
    rec = runner.run(DEP)
    world.api_status = 200
    assert rec["state"] == "ABORTED" and rec["stop_reason"] == "precheck_failed"
    assert calls(world, "stop") == []


def test_expected_status_is_not_tolerated_during_recovery(env):
    _, world, docker, ctl, runner = env
    real_start = docker.start

    def start_but_still_broken(name):
        real_start(name)
        world.api_status = 500                        # store is back, API still failing
    docker.start = start_but_still_broken
    rec = runner.run(DEP)
    world.api_status = 200
    assert rec["rollback"]["verified"] and not rec["recovery"]["verified"]
    assert rec["state"] == "LATCHED_UNSAFE" and ctl.latched


def test_500_still_aborts_other_experiments(env):
    _, world, _, _, runner = env
    import threading

    def flip():
        for _ in range(400):
            if world.forwarder_down():
                world.api_status = 500
                return
            time.sleep(0.01)
    threading.Thread(target=flip, daemon=True).start()
    rec = runner.run({"experiment_type": "service_crash", "target": "fms-forwarder", "duration_s": 5, "approved_by": "t"})
    world.api_status = 200
    assert rec["stop_reason"] == "guardrail_error_rate"


def test_neighbour_state_change_during_outage_trips_blast_radius(env):
    _, world, _, ctl, runner = env
    import threading

    def crash_neighbour():
        for _ in range(400):
            if world.influx_down():
                world.mutate("fms-forwarder", running=False, status="exited")   # e.g. a dependant crashing
                return
            time.sleep(0.01)
    threading.Thread(target=crash_neighbour, daemon=True).start()
    rec = runner.run({**DEP, "duration_s": 5})
    assert rec["stop_reason"] == "guardrail_blast_radius"
    assert rec["guardrails"]["triggered"]["detail"]["changes"][0]["container"] == "fms-forwarder"
    assert world.containers["influxDB"].running, "the injected fault is still rolled back"
    assert rec["state"] == "LATCHED_UNSAFE" and ctl.latched


def test_startup_recovery_starts_influx_only_with_matching_marker(env):
    cfg, world, docker, ctl, _ = env
    ctl.mark_fault_active("dead", "influxDB", "docker_stop", container_id=world.containers["influxDB"].id)
    docker.stop("influxDB", 2)
    out = SafetyController(cfg, CATALOG, docker).startup_recovery()
    assert out["outcome"] == "influxDB: started" and world.containers["influxDB"].running


def test_reset_refused_while_influx_is_stopped(env):
    cfg, world, docker, ctl, _ = env
    ctl.latch("test")
    docker.stop("influxDB", 2)
    with pytest.raises(Exception):
        ctl.reset("tester", "influx still down")
    docker.start("influxDB")
    ctl.reset("tester", "influx running")
    assert not ctl.latched


def test_data_gap_recorded_for_other_experiments_too(env):
    _, world, _, _, runner = env
    rec = runner.run({"experiment_type": "telemetry_stall", "target": "fms-forwarder", "duration_s": 1, "approved_by": "t"})
    assert rec["data_gap"]["max_gap_s"] >= 0.9
    assert rec["impact"]["silent_failure"] is True, "HTTP 200 while stale, never an HTTP error"


# ---------------- PR #16 safety-review regressions ----------------
from dataclasses import replace as _replace  # noqa: E402


def test_B_declared_symptom_cannot_be_combined_with_error_tolerance():
    with pytest.raises(ValueError):
        ExperimentSpec("x", "docker_stop", ("influxDB",), 10, expected_statuses=(500,), max_error_rate=0.3)


def test_B_global_tolerance_is_forced_to_zero_when_a_symptom_is_declared(tmp_path):
    from resiliencepilot.config import Guardrails
    cfg = make_config(tmp_path, guardrails=Guardrails(max_error_rate=0.5))
    world = FakeWorld()
    docker = FakeDocker(world)
    runner = ExperimentRunner(cfg, CATALOG, SafetyController(cfg, CATALOG, docker), docker, FakeObserver(world),
                              EvidenceStore(cfg.data_dir))
    flips = iter([500, 500, 500, 503] + [500] * 1000)
    orig = runner.observer.observe

    def mostly_500_then_one_503():
        if world.influx_down():
            world.influx_down_status = next(flips)
        return orig()
    runner.observer.observe = mostly_500_then_one_503
    rec = runner.run({**DEP, "duration_s": 5})
    world.influx_down_status = 500
    assert rec["guardrails"]["config"]["max_error_rate"] == 0.0
    assert rec["stop_reason"] == "guardrail_error_rate", "a single 503 among 500s still aborts"
    assert rec["guardrails"]["triggered"]["detail"]["last_status"] == 503


def test_C_data_gap_failure_never_changes_the_outcome(env):
    _, world, _, ctl, runner = env
    runner.observer.statuses_between = lambda s, e: ["not-a-datetime", 42]     # malformed data
    rec = runner.run(DEP)
    assert rec["state"] == "COMPLETED" and not ctl.latched
    assert "error" in rec["data_gap"]


def test_C_data_gap_query_exception_is_recorded(env):
    _, world, _, ctl, runner = env

    def boom(s, e):
        raise RuntimeError("window query failed")
    runner.observer.statuses_between = boom
    rec = runner.run(DEP)
    assert rec["state"] == "COMPLETED" and "window query failed" in rec["data_gap"]["error"]


def test_D_recovery_lower_bound_is_the_verified_injection_when_all_fault_polls_are_500(env):
    _, world, docker, _, runner = env
    rec = runner.run(DEP)
    assert all(o["observation"]["http_status"] == 500 for o in rec["observations"] if o["phase"] == "fault")
    lb = rec["recovery"]["lower_bound"]
    assert lb["source"].startswith("verified injection time")
    first_counted = next(o for o in rec["observations"] if o["phase"] == "recovery" and o["observation"]["http_status"] == 200)
    from datetime import datetime
    # nothing created before the bound may have been counted: the first counted advance is after it
    assert rec["recovery"]["verified"]
    assert datetime.fromisoformat(lb["at"]) > datetime.fromisoformat(rec["impact"]["injected_at"])


def test_E_first_expected_status_kept_even_if_the_loop_raises(env):
    _, world, _, ctl, runner = env
    orig = runner.observer.observe
    seen = {"n": 0}

    def raise_after_expected():
        o = orig()
        if world.influx_down():
            seen["n"] += 1
            if seen["n"] == 2:
                raise RuntimeError("observer bug mid-window")
        return o
    runner.observer.observe = raise_after_expected
    rec = runner.run({**DEP, "duration_s": 5})
    assert rec["stop_reason"] == "error"
    assert rec["impact"]["time_to_first_expected_status_s"] is not None
    assert rec["rollback"]["verified"]
