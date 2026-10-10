"""Automatic abort + rollback guardrails: error rate, latency p95 and blast radius."""
import threading
import time
from dataclasses import replace

import pytest

from resiliencepilot.config import ExperimentSpec, Guardrails
from resiliencepilot.docker_cli import SnapshotEntry
from resiliencepilot.evidence import EvidenceStore
from resiliencepilot.guardrails import (ErrorRateGuard, LatencyGuard, blast_radius_changes, latency_threshold, p95)
from resiliencepilot.orchestrator import ExperimentRunner
from resiliencepilot.safety import SafetyController

from helpers import FakeDocker, FakeObserver, FakeWorld, make_config

CATALOG = {
    "telemetry_stall": ExperimentSpec("telemetry_stall", "docker_pause", ("fms-forwarder",), 30),
    "service_crash": ExperimentSpec("service_crash", "docker_stop", ("fms-forwarder",), 15),
}
STALL = {"experiment_type": "telemetry_stall", "target": "fms-forwarder", "duration_s": 2, "approved_by": "tester"}
CRASH = {**STALL, "experiment_type": "service_crash"}


def build(tmp_path, catalog=CATALOG, **cfg_overrides):
    cfg = make_config(tmp_path, **cfg_overrides)
    world = FakeWorld()
    docker = FakeDocker(world)
    ctl = SafetyController(cfg, catalog, docker)
    runner = ExperimentRunner(cfg, catalog, ctl, docker, FakeObserver(world), EvidenceStore(cfg.data_dir))
    return world, ctl, runner


def when_faulted(world, action):
    """Run `action` once the forwarder is down (paused or stopped)."""
    def go():
        for _ in range(400):
            if world.forwarder_down():
                action()
                return
            time.sleep(0.01)
    threading.Thread(target=go, daemon=True).start()


# ---------------- pure logic ----------------
def test_p95_nearest_rank():
    assert p95([]) is None
    assert p95([5.0]) == 5.0
    assert p95(list(range(1, 21))) == 19            # ceil(0.95*20)=19th smallest
    assert p95([1, 2, 3, 100]) == 100


def test_error_rate_guard_zero_tolerance_trips_on_first_error():
    g = ErrorRateGuard(0.0)
    assert g.update(200) is None and g.update(None) is None   # no response is not an HTTP error
    v = g.update(500)
    assert v.guard == "error_rate" and v.detail["errors"] == 1 and v.detail["responses"] == 2


def test_error_rate_guard_with_tolerance():
    g = ErrorRateGuard(0.5, min_samples=5)
    assert g.update(500) is None                                          # 1/1 but too few samples to judge
    assert [g.update(s) for s in (200, 200, 500)] == [None, None, None]   # 2/4, still < min_samples
    assert g.update(500).detail["error_rate"] == 0.6                      # 3/5 > 0.5 with 5 samples


def test_error_rate_guard_with_tolerance_does_not_trip_at_or_below_limit():
    g = ErrorRateGuard(0.5, min_samples=4)
    assert [g.update(s) for s in (200, 500, 200, 500, 200, 500)] == [None] * 6   # 2/4, 2/5, 3/6: never above 50 %


def test_latency_guard_needs_min_samples_then_uses_rolling_p95():
    g = LatencyGuard(threshold_ms=100, window=5, min_samples=3)
    assert g.update(500) is None and g.update(500) is None   # below min_samples: no decision yet
    assert g.update(500).guard == "latency_p95"
    g2 = LatencyGuard(threshold_ms=100, window=3, min_samples=3)
    for v in (500, 10, 10, 10):                              # the old spike rolls out of the window
        r = g2.update(v)
    assert r is None


def test_latency_threshold_uses_baseline_multiplier_with_floor():
    assert latency_threshold(26.4, 5.0, 250.0) == 250.0       # 5x baseline below the floor
    assert latency_threshold(100.0, 5.0, 250.0) == 500.0
    assert latency_threshold(None, 5.0, 250.0) == 250.0


def test_blast_radius_diff():
    a = SnapshotEntry("a" * 64, "running", "t0")
    base = {"fms-forwarder": a, "influxDB": SnapshotEntry("b" * 64, "running", "t0")}
    assert blast_radius_changes(base, {**base, "fms-forwarder": replace(a, status="exited")}, {"fms-forwarder"}) == []
    restarted = {**base, "influxDB": SnapshotEntry("b" * 64, "running", "t1")}
    assert blast_radius_changes(base, restarted, {"fms-forwarder"})[0]["container"] == "influxDB"
    removed = {"fms-forwarder": a}
    assert blast_radius_changes(base, removed, {"fms-forwarder"})[0]["after"] is None
    added = {**base, "intruder": SnapshotEntry("c" * 64, "running", "t0")}
    assert blast_radius_changes(base, added, {"fms-forwarder"})[0]["container"] == "intruder"


@pytest.mark.parametrize("bad", [dict(error_min_samples=0), dict(max_error_rate=1.0), dict(max_error_rate=-0.1), dict(latency_p95_multiplier=0.5),
                                 dict(latency_p95_floor_ms=0), dict(latency_min_samples=20, latency_window=10),
                                 dict(blast_radius_interval_s=5), dict(blast_radius_max_snapshot_failures=0)])
def test_guardrail_config_cannot_be_loosened_past_bounds(bad):
    with pytest.raises(ValueError):
        Guardrails(**bad)


def test_spec_blast_radius_must_cover_targets():
    with pytest.raises(ValueError):
        ExperimentSpec("x", "docker_stop", ("fms-forwarder",), 10, blast_radius=("influxDB",))
    with pytest.raises(ValueError):
        ExperimentSpec("x", "docker_kill", ("fms-forwarder",), 10)


# ---------------- end to end: each guard aborts and the fault is still rolled back ----------------
@pytest.mark.parametrize("req", [STALL, CRASH], ids=["stall", "crash"])
def test_clean_run_arms_guards_and_trips_none(tmp_path, req):
    world, ctl, runner = build(tmp_path)
    rec = runner.run(req)
    g = rec["guardrails"]
    assert rec["state"] == "COMPLETED" and g["triggered"] is None
    assert g["latency_threshold_ms"] == 250.0 and "precheck" in g["latency_baseline_source"]
    assert g["blast_radius_allowed"] == ["fms-forwarder"]
    assert set(g["blast_radius_baseline"]) == {"fms-forwarder", "influxDB"}
    assert g["final_blast_check"]["clean"] is True
    assert any(e["event"] == "guardrails_armed" for e in rec["timeline"])


@pytest.mark.parametrize("req", [STALL, CRASH], ids=["stall", "crash"])
def test_error_rate_trips_and_rolls_back(tmp_path, req):
    world, ctl, runner = build(tmp_path)
    when_faulted(world, lambda: setattr(world, "api_status", 500))
    rec = runner.run({**req, "duration_s": 5})
    world.api_status = 200
    assert rec["stop_reason"] == "guardrail_error_rate"
    assert rec["guardrails"]["triggered"]["guard"] == "error_rate"
    assert rec["rollback"]["verified"] and not world.forwarder_down()


def test_error_rate_tolerance_is_per_experiment(tmp_path):
    cat = {**CATALOG, "telemetry_stall": ExperimentSpec("telemetry_stall", "docker_pause", ("fms-forwarder",), 30,
                                                        max_error_rate=0.9)}
    world, ctl, runner = build(tmp_path, catalog=cat)
    flips = iter([500, 200] * 1000)
    orig = runner.observer.observe

    def flaky():
        if world.forwarder_down():
            world.api_status = next(flips)
        else:
            world.api_status = 200
        return orig()
    runner.observer.observe = flaky
    rec = runner.run({**STALL, "duration_s": 1})
    assert rec["stop_reason"] == "duration_elapsed", "50 % errors are tolerated when max_error_rate is 0.9"
    assert rec["guardrails"]["config"]["max_error_rate"] == 0.9
    assert rec["impact"]["http_error_rate_during_fault"] > 0


@pytest.mark.parametrize("req", [STALL, CRASH], ids=["stall", "crash"])
def test_latency_p95_trips_and_rolls_back(tmp_path, req):
    world, ctl, runner = build(tmp_path)
    when_faulted(world, lambda: setattr(world, "latency_ms", 900.0))
    rec = runner.run({**req, "duration_s": 5})
    assert rec["stop_reason"] == "guardrail_latency"
    d = rec["guardrails"]["triggered"]["detail"]
    assert d["p95_ms"] == 900.0 and d["threshold_ms"] == 250.0 and d["samples"] >= 5
    assert rec["rollback"]["verified"] and not world.forwarder_down()


def test_latency_threshold_prefers_the_stored_baseline(tmp_path):
    world, ctl, runner = build(tmp_path)
    runner.store.save_baseline("baseline-20990101T000000Z", {"summary": {"latency_ms": {"p95": 100.0}}})
    rec = runner.run({**STALL, "duration_s": 1})
    g = rec["guardrails"]
    assert g["latency_baseline_p95_ms"] == 100.0 and g["latency_threshold_ms"] == 500.0
    assert g["latency_baseline_source"] == "baseline baseline-20990101T000000Z"


@pytest.mark.parametrize("req", [STALL, CRASH], ids=["stall", "crash"])
def test_blast_radius_breach_trips_rolls_back_and_latches(tmp_path, req):
    world, ctl, runner = build(tmp_path)
    when_faulted(world, lambda: world.mutate("influxDB", started_at="2026-10-10T09:59:59Z"))   # neighbour restarted
    rec = runner.run({**req, "duration_s": 5})
    assert rec["stop_reason"] == "guardrail_blast_radius"
    change = rec["guardrails"]["triggered"]["detail"]["changes"][0]
    assert change["container"] == "influxDB"
    assert rec["rollback"]["verified"] and not world.forwarder_down()
    assert rec["recovery"]["verified"]
    assert rec["state"] == "LATCHED_UNSAFE" and ctl.latched, "something outside our control changed: a human must check"


def test_blast_radius_unobservable_trips(tmp_path):
    world, ctl, runner = build(tmp_path)
    when_faulted(world, lambda: setattr(world, "snapshot_fails", True))
    rec = runner.run({**STALL, "duration_s": 5})
    world.snapshot_fails = False
    assert rec["stop_reason"] == "guardrail_blast_radius"
    assert rec["guardrails"]["triggered"]["detail"]["unobservable"] is True
    assert rec["rollback"]["verified"]


def test_refuses_to_inject_when_blast_radius_cannot_be_observed(tmp_path):
    world, ctl, runner = build(tmp_path)
    world.snapshot_fails = True
    rec = runner.run(STALL)
    assert rec["state"] == "ABORTED" and rec["stop_reason"] == "precheck_failed"
    assert [c for c in world.calls if c[1] in ("pause", "stop")] == []


def test_breach_after_the_fault_window_still_latches(tmp_path):
    world, ctl, runner = build(tmp_path)
    rb = runner.ctl.clear_fault_marker

    def breach_then_clear():
        world.mutate("influxDB", status="restarting")      # change lands after rollback, before the final check
        rb()
    runner.ctl.clear_fault_marker = breach_then_clear
    rec = runner.run({**STALL, "duration_s": 1})
    assert rec["stop_reason"] == "duration_elapsed" and rec["recovery"]["verified"]
    assert rec["guardrails"]["final_blast_check"]["clean"] is False
    assert rec["state"] == "LATCHED_UNSAFE" and ctl.latched
