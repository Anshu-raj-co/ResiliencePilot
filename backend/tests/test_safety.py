"""Phase 2 required cases: success, timeout, cancellation, invalid target, malformed request, concurrent request,
observer loss, rollback failure, recovery failure — plus crash recovery, precheck refusal and reset rules."""
import threading
import time

import pytest

from resiliencepilot.config import ExperimentSpec
from resiliencepilot.evidence import EvidenceStore
from resiliencepilot.orchestrator import ExperimentRunner
from resiliencepilot.safety import Busy, Latched, RequestRejected, SafetyController, SafetyError

from helpers import FakeDocker, FakeObserver, FakeWorld, make_config

CATALOG = {"telemetry_stall": ExperimentSpec("telemetry_stall", "docker_pause", ("fms-forwarder",), 30)}
GOOD = {"experiment_type": "telemetry_stall", "target": "fms-forwarder", "duration_s": 1, "approved_by": "tester"}


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path)
    world = FakeWorld()
    docker = FakeDocker(world)
    observer = FakeObserver(world)
    ctl = SafetyController(cfg, CATALOG, docker, observer=observer)
    runner = ExperimentRunner(cfg, CATALOG, ctl, docker, observer, EvidenceStore(cfg.data_dir))
    return cfg, world, docker, observer, ctl, runner


def calls(world, kind):
    return [c for c in world.calls if c[1] == kind]


# ---------------- success ----------------
def test_success_injects_detects_rolls_back_and_verifies_recovery(env):
    cfg, world, _, _, ctl, runner = env
    rec = runner.run(GOOD)
    assert rec["state"] == "COMPLETED"
    assert rec["stop_reason"] == "duration_elapsed"
    assert rec["impact"]["fault_verified"] and rec["impact"]["stale_detected"]
    assert rec["impact"]["healthy_but_stale_observations"] > 0
    assert rec["impact"]["http_ok_ratio_during_fault"] == 1.0
    assert rec["rollback"] == {"attempted": True, "verified": True, "by": "orchestrator", "detail": "target running and unpaused"} \
        or rec["rollback"]["verified"]
    assert rec["recovery"]["verified"]
    assert not world.forwarder_paused()
    assert len(calls(world, "pause")) == 1 and len(calls(world, "unpause")) == 1
    assert ctl.status()["state"] == "READY" and ctl.active_fault_marker() is None
    phases = {o["phase"] for o in rec["observations"]}
    assert phases == {"precheck", "fault", "recovery"}


# ---------------- validation ----------------
@pytest.mark.parametrize("bad", [
    {**GOOD, "target": "influxDB"},                       # real container, not allowlisted
    {**GOOD, "target": "fms-server; rm -rf /"},           # injection attempt
    {**GOOD, "experiment_type": "network_delay"},         # unknown type
    {**GOOD, "duration_s": 31},                           # over hard cap
    {**GOOD, "duration_s": 0},
    {**GOOD, "duration_s": "5"},                          # strict typing: no coercion
    {**GOOD, "duration_s": 2.5},
    {**GOOD, "approved_by": "   "},                       # no anonymous approval
    {k: v for k, v in GOOD.items() if k != "approved_by"},
    {**GOOD, "shell": "docker kill fms-server"},          # unknown field
    [],                                                   # not an object
    "telemetry_stall",
])
def test_malformed_or_invalid_requests_rejected_without_side_effects(env, bad):
    _, world, _, _, ctl, runner = env
    with pytest.raises(RequestRejected):
        runner.run(bad)
    assert world.calls == []
    assert ctl.status()["state"] == "READY"


def test_config_duration_limit_lower_than_30_is_enforced(tmp_path):
    cfg = make_config(tmp_path, max_duration_s=5)
    ctl = SafetyController(cfg, CATALOG, FakeDocker(FakeWorld()))
    with pytest.raises(RequestRejected):
        ctl.validate({**GOOD, "duration_s": 6})


def test_target_from_other_compose_project_refused(env):
    _, world, _, _, ctl, runner = env
    from dataclasses import replace
    world.containers["fms-forwarder"] = replace(world.containers["fms-forwarder"], project="someone-elses-stack")
    rec = runner.run(GOOD)
    assert rec["state"] == "ABORTED" and rec["stop_reason"] == "precheck_failed"
    assert calls(world, "pause") == []


# ---------------- concurrency ----------------
def test_concurrent_request_rejected_while_active(env):
    _, world, _, _, ctl, runner = env
    run_id = runner.start({**GOOD, "duration_s": 2})
    time.sleep(0.3)
    with pytest.raises(Busy):
        runner.run(GOOD)
    runner.threads[run_id].join(10)
    assert len(calls(world, "pause")) == 1
    assert ctl.status()["state"] == "READY"


# ---------------- cancellation / emergency stop ----------------
def test_emergency_stop_rolls_back_early_and_blocks_new_runs_until_reset(env):
    _, world, _, _, ctl, runner = env
    run_id = runner.start({**GOOD, "duration_s": 10})
    for _ in range(100):
        if world.forwarder_paused():
            break
        time.sleep(0.02)
    t_stop = time.monotonic()
    ctl.emergency_stop("test")
    runner.threads[run_id].join(10)
    rec = runner.store.load_run(run_id)
    assert rec["stop_reason"] == "emergency_stop"
    assert rec["state"] == "ABORTED", "only an as-planned run may be COMPLETED"
    assert calls(world, "unpause")[0][0] - t_stop < 0.5  # rolled back promptly, not after 10 s
    assert rec["rollback"]["verified"] and rec["recovery"]["verified"]
    with pytest.raises(Latched):
        runner.run(GOOD)
    ctl.reset("tester", "checked stack")
    assert runner.run(GOOD)["state"] == "COMPLETED"


def test_kill_switch_file_is_honoured(env):
    _, world, _, _, ctl, runner = env
    run_id = runner.start({**GOOD, "duration_s": 10})
    for _ in range(100):
        if world.forwarder_paused():
            break
        time.sleep(0.02)
    ctl.stop_file.write_text("{}")  # what scripts/kill-switch.sh does
    runner.threads[run_id].join(10)
    assert runner.store.load_run(run_id)["stop_reason"] == "emergency_stop"
    assert not world.forwarder_paused()


# ---------------- timeout / watchdog ----------------
def test_watchdog_rolls_back_when_orchestrator_loop_hangs(env):
    cfg, world, _, observer, ctl, runner = env
    world.block_observe_s = 3.0
    observer.block_once_when_paused = True   # first observation during the fault hangs for 3 s
    t0 = time.monotonic()
    rec = runner.run(GOOD)  # duration 1 s + grace 0.5 s => watchdog at ~1.5 s after injection
    unpause_t = calls(world, "unpause")[0][0]
    pause_t = calls(world, "pause")[0][0]
    assert unpause_t - pause_t < 2.2, "watchdog must unpause before the hung observation returns"
    assert rec["stop_reason"] == "watchdog_timeout"
    assert rec["rollback"]["by"] == "watchdog"
    assert rec["recovery"]["verified"]
    assert not world.forwarder_paused()


# ---------------- observer loss / safety threshold ----------------
def test_observer_loss_during_fault_triggers_rollback(env):
    _, world, docker, _, ctl, runner = env
    orig_pause = docker.pause

    def pause_and_break_api(name):
        orig_pause(name)
        world.api_down = True
    docker.pause = pause_and_break_api

    def restore_api(name, _orig=docker.unpause):
        world.api_down = False
        _orig(name)
    docker.unpause = restore_api
    rec = runner.run({**GOOD, "duration_s": 5})
    assert rec["stop_reason"] == "observer_lost"
    assert rec["rollback"]["verified"]
    assert not world.forwarder_paused()


# ---------------- rollback failure ----------------
def test_rollback_failure_latches_and_blocks_everything(env):
    _, world, _, _, ctl, runner = env
    world.unpause_fails = True
    rec = runner.run(GOOD)
    assert rec["state"] == "LATCHED_UNSAFE"
    assert not rec["rollback"]["verified"]
    assert ctl.status()["state"] == "LATCHED_UNSAFE"
    assert ctl.active_fault_marker() is not None, "marker must survive so a later process can clean up"
    with pytest.raises(Latched):
        runner.run(GOOD)
    with pytest.raises(SafetyError):
        ctl.reset("tester", "container still paused")   # reset refused while target is paused
    world.unpause_fails = False
    docker = runner.docker
    docker.unpause("fms-forwarder")                      # human fixes the stack
    ctl.reset("tester", "manually unpaused")
    assert ctl.status()["state"] == "READY"


# ---------------- recovery failure ----------------
def test_recovery_failure_latches_even_though_container_is_running(env):
    _, world, _, _, ctl, runner = env
    world.stay_stale_after_unpause = True   # container unpaused, API 200, but telemetry never advances
    rec = runner.run(GOOD)
    assert rec["rollback"]["verified"]
    assert not rec["recovery"]["verified"]
    assert rec["state"] == "LATCHED_UNSAFE"
    assert ctl.latched


# ---------------- fault not verified ----------------
def test_fault_not_verified_is_not_reported_as_success(env):
    _, world, _, _, _, runner = env
    world.pause_is_noop = True
    rec = runner.run(GOOD)
    assert rec["stop_reason"] == "fault_not_verified"
    assert rec["state"] == "ABORTED"
    assert rec["impact"]["fault_verified"] is False


# ---------------- precheck ----------------
def test_precheck_refuses_injection_when_telemetry_already_stale(env):
    _, world, _, _, _, runner = env
    world.containers  # forwarder running but telemetry frozen from the start
    world.stay_stale_after_unpause = True
    world.calls.append((0, "unpause", "x"))  # makes the fake freeze telemetry
    rec = runner.run(GOOD)
    assert rec["state"] == "ABORTED" and rec["stop_reason"] == "precheck_failed"
    assert calls(world, "pause") == []


# ---------------- interrupted process ----------------
def test_startup_recovery_unpauses_and_latches_after_crash(env):
    cfg, world, docker, _, ctl, _ = env
    ctl.mark_fault_active("dead-run", "fms-forwarder", "docker_pause")
    docker.pause("fms-forwarder")
    fresh_ctl = SafetyController(cfg, CATALOG, docker, observer=FakeObserver(world))    # a new process
    out = fresh_ctl.startup_recovery()
    assert out["outcome"] == "fms-forwarder: unpaused"
    assert not world.forwarder_paused()
    assert fresh_ctl.latched
    fresh_ctl.reset("tester", "post-crash check")
    assert fresh_ctl.active_fault_marker() is None and not fresh_ctl.latched


def test_latch_persists_across_controller_instances(env):
    cfg, _, docker, _, ctl, _ = env
    ctl.latch("test")
    assert SafetyController(cfg, CATALOG, docker).latched


def test_reset_requires_operator(env):
    *_, ctl, _ = env
    with pytest.raises(SafetyError):
        ctl.reset("  ", "x")


# ---------------- safety-review regressions (docs/testing/SAFETY_REVIEW.md) ----------------
import json
import os

from resiliencepilot.safety import pid_alive


def _write_lock(cfg, pid, run_id="other-run"):
    (cfg.data_dir / "run.lock").write_text(json.dumps({"pid": pid, "run_id": run_id}))


def test_pid_alive_never_kills_and_detects_dead():
    assert pid_alive(os.getpid())
    assert not pid_alive(2_000_000_000)


def test_H1_run_lock_excludes_other_process(env):
    cfg, world, docker, _, ctl, runner = env
    _write_lock(cfg, os.getppid())                      # a live process that is not us
    with pytest.raises(Busy):
        runner.run(GOOD)
    with pytest.raises(Busy):
        ctl.reset("tester", "must not reset under a live run")
    assert world.calls == []


def test_H1_second_controller_same_process_is_busy(env):
    cfg, _, docker, _, ctl, _ = env
    ctl.begin("run-a")
    other = SafetyController(cfg, CATALOG, docker)
    with pytest.raises(Busy):
        other.begin("run-b")
    ctl.end("run-a")
    assert not (cfg.data_dir / "run.lock").exists()


def test_H1_stale_lock_from_dead_process_latches(env):
    cfg, world, docker, _, ctl, runner = env
    _write_lock(cfg, 2_000_000_000, "dead-run")
    with pytest.raises(Latched):
        runner.run(GOOD)
    out = ctl.startup_recovery()
    assert out is not None and ctl.latched
    ctl.reset("tester", "checked")
    assert runner.run(GOOD)["state"] == "COMPLETED"


def test_M1_startup_recovery_does_not_touch_a_live_run(env):
    cfg, world, docker, _, ctl, _ = env
    _write_lock(cfg, os.getppid(), "live-run")
    ctl.mark_fault_active("live-run", "fms-forwarder", "docker_pause")
    docker.pause("fms-forwarder")
    assert SafetyController(cfg, CATALOG, docker).startup_recovery() is None
    assert world.forwarder_paused() and not ctl.latched


def test_M2_late_landing_pause_is_still_rolled_back(env):
    _, world, _, _, _, runner = env
    world.pause_delay_s = 2.0          # watchdog fires at +1.5 s while `docker pause` is still in flight
    rec = runner.run(GOOD)
    assert not world.forwarder_paused(), "container must not be left paused"
    assert rec["rollback"]["verified"]
    assert len(calls(world, "unpause")) >= 1


def test_M3_watchdog_grace_is_bounded(tmp_path):
    with pytest.raises(ValueError):
        make_config(tmp_path, watchdog_grace_s=1000)


def test_M4_corrupt_marker_and_state_are_unsafe(env):
    cfg, world, docker, _, ctl, runner = env
    (cfg.data_dir / "active_fault.json").write_text('{"run_id": "x", "tar')   # truncated write
    docker.pause("fms-forwarder")
    out = ctl.startup_recovery()
    assert "unpaused" in out["outcome"] and ctl.latched and not world.forwarder_paused()
    (cfg.data_dir / "safety_state.json").write_text("not json")
    assert ctl.latched
    with pytest.raises(Latched):
        runner.run(GOOD)
    ctl.reset("tester", "repaired")
    assert not ctl.latched


def test_M5_signal_stop_is_lock_free(env):
    _, world, _, _, ctl, runner = env
    run_id = runner.start({**GOOD, "duration_s": 10})
    for _ in range(100):
        if world.forwarder_paused():
            break
        time.sleep(0.02)
    with ctl._lock:                     # even while the controller lock is held...
        ctl.signal_stop()               # ...the signal path must not block
    runner.threads[run_id].join(10)
    assert runner.store.load_run(run_id)["stop_reason"] == "emergency_stop"


def test_watchdog_rollback_failure_latches(env):
    _, world, _, observer, ctl, runner = env
    world.unpause_fails = True
    world.block_observe_s = 2.0
    observer.block_once_when_paused = True
    rec = runner.run(GOOD)
    assert rec["state"] == "LATCHED_UNSAFE" and ctl.latched


def test_L1_completed_only_when_as_planned(env):
    _, world, docker, _, _, runner = env
    orig = docker.pause

    def pause_and_break(name):
        orig(name)
        world.api_down = True
    docker.pause = pause_and_break
    docker.unpause = (lambda n, _u=docker.unpause: (setattr(world, "api_down", False), _u(n)))
    rec = runner.run({**GOOD, "duration_s": 5})
    assert rec["stop_reason"] == "observer_lost" and rec["state"] == "ABORTED"


def test_run_record_states_the_config_profile(env):
    *_, runner = env
    assert runner.run(GOOD)["config_profile"] == "default"


def test_run_record_states_the_host_platform(env):
    *_, runner = env
    rec = runner.run(GOOD)
    assert set(rec["platform"]) == {"os", "os_release", "cpu"} and rec["platform"]["cpu"]
