"""service_crash: docker stop / docker start of fms-forwarder, with the same safety guarantees as telemetry_stall."""
import time

import pytest

from resiliencepilot.config import ExperimentSpec
from resiliencepilot.evidence import EvidenceStore
from resiliencepilot.faults import PauseFault, StopStartFault, make_fault
from resiliencepilot.orchestrator import ExperimentRunner
from resiliencepilot.safety import Latched, RequestRejected, SafetyController, SafetyError

from helpers import FakeDocker, FakeObserver, FakeWorld, make_config

CATALOG = {
    "telemetry_stall": ExperimentSpec("telemetry_stall", "docker_pause", ("fms-forwarder",), 30),
    "service_crash": ExperimentSpec("service_crash", "docker_stop", ("fms-forwarder",), 15, stop_timeout_s=2),
}
CRASH = {"experiment_type": "service_crash", "target": "fms-forwarder", "duration_s": 1, "approved_by": "tester"}


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path)
    world = FakeWorld()
    docker = FakeDocker(world)
    observer = FakeObserver(world)
    ctl = SafetyController(cfg, CATALOG, docker)
    runner = ExperimentRunner(cfg, CATALOG, ctl, docker, observer, EvidenceStore(cfg.data_dir))
    return cfg, world, docker, observer, ctl, runner


def calls(world, kind):
    return [c for c in world.calls if c[1] == kind]


def fwd(world):
    return world.containers["fms-forwarder"]


def test_factory_maps_mechanisms():
    d = FakeDocker(FakeWorld())
    assert isinstance(make_fault(CATALOG["telemetry_stall"], d, "fms-forwarder"), PauseFault)
    f = make_fault(CATALOG["service_crash"], d, "fms-forwarder")
    assert isinstance(f, StopStartFault) and f.stop_timeout_s == 2


def test_success_stop_detect_start_and_verify_recovery(env):
    _, world, _, _, ctl, runner = env
    id_before = fwd(world).id
    rec = runner.run(CRASH)
    assert rec["state"] == "COMPLETED" and rec["stop_reason"] == "duration_elapsed"
    assert rec["impact"]["fault_verified"] and rec["impact"]["stale_detected"]
    assert rec["impact"]["http_ok_ratio_during_fault"] == 1.0
    assert rec["rollback"]["verified"] and rec["recovery"]["verified"]
    assert "same container" in rec["recovery"]["criteria"]
    assert len(calls(world, "stop")) == 1 and len(calls(world, "start")) == 1
    assert calls(world, "pause") == [] and calls(world, "unpause") == []
    assert fwd(world).running and fwd(world).id == id_before
    fault_active = next(e for e in rec["timeline"] if e["event"] == "fault_active")
    assert fault_active["detail"]["verified_by"] == "docker inspect State.Running == false"
    assert ctl.status()["state"] == "READY" and ctl.active_fault_marker() is None


def test_duration_cap_from_catalogue(env):
    *_, ctl, _ = env
    with pytest.raises(RequestRejected):
        ctl.validate({**CRASH, "duration_s": 16})      # service_crash is capped at 15 s, stricter than the hard 30 s
    assert ctl.validate({**CRASH, "duration_s": 15}).duration_s == 15


@pytest.mark.parametrize("bad", [{**CRASH, "target": "influxDB"}, {**CRASH, "experiment_type": "service_kill"},
                                 {**CRASH, "stop_timeout_s": 0}])
def test_invalid_requests_rejected(env, bad):
    _, world, _, _, _, runner = env
    with pytest.raises(RequestRejected):
        runner.run(bad)
    assert world.calls == []


def test_start_failure_latches_and_keeps_marker(env):
    _, world, _, _, ctl, runner = env
    world.start_fails = True
    rec = runner.run(CRASH)
    assert rec["state"] == "LATCHED_UNSAFE" and not rec["rollback"]["verified"]
    assert ctl.latched and ctl.active_fault_marker() is not None
    with pytest.raises(Latched):
        runner.run(CRASH)
    with pytest.raises(SafetyError):
        ctl.reset("tester", "target still stopped")    # reset refuses while the target is down
    world.start_fails = False
    runner.docker.start("fms-forwarder")
    ctl.reset("tester", "started manually")
    assert ctl.status()["state"] == "READY"


def test_stop_that_does_nothing_is_not_reported_as_a_fault(env):
    _, world, _, _, _, runner = env
    world.stop_is_noop = True
    rec = runner.run(CRASH)
    assert rec["stop_reason"] == "fault_not_verified" and rec["state"] == "ABORTED"
    assert rec["impact"]["fault_verified"] is False


def test_replaced_container_is_never_restarted_and_latches(env):
    _, world, _, _, ctl, runner = env
    world.replace_on_stop = True                     # someone recreated the container during the fault
    rec = runner.run(CRASH)
    assert rec["stop_reason"] == "fault_not_verified"      # the stopped container is not the one we recorded
    assert not rec["rollback"]["verified"] and "replaced" in rec["rollback"]["detail"]
    assert rec["state"] == "LATCHED_UNSAFE" and ctl.latched
    assert calls(world, "start") == [], "must not start a container it did not stop"
    assert not rec["recovery"]["verified"]


def test_watchdog_starts_the_container_when_loop_hangs(env):
    _, world, _, observer, _, runner = env
    world.block_observe_s = 3.0
    observer.block_once_when_paused = True           # first fault-window observation hangs
    rec = runner.run(CRASH)                          # duration 1 s + grace 0.5 s
    start_t, stop_t = calls(world, "start")[0][0], calls(world, "stop")[0][0]
    assert start_t - stop_t < 2.2, "watchdog must start the container before the hung observation returns"
    assert rec["stop_reason"] == "watchdog_timeout" and rec["rollback"]["by"] == "watchdog"
    assert rec["recovery"]["verified"] and fwd(world).running


def test_emergency_stop_starts_container_early(env):
    _, world, _, _, ctl, runner = env
    run_id = runner.start({**CRASH, "duration_s": 10})
    for _ in range(200):
        if not fwd(world).running:
            break
        time.sleep(0.02)
    t_stop = time.monotonic()
    ctl.emergency_stop("test")
    runner.threads[run_id].join(10)
    rec = runner.store.load_run(run_id)
    assert rec["stop_reason"] == "emergency_stop" and rec["state"] == "ABORTED"
    assert calls(world, "start")[0][0] - t_stop < 0.5
    assert rec["rollback"]["verified"] and rec["recovery"]["verified"]


def test_startup_recovery_starts_a_stopped_target_and_latches(env):
    cfg, world, docker, _, ctl, _ = env
    ctl.mark_fault_active("dead-run", "fms-forwarder", "docker_stop")
    docker.stop("fms-forwarder", 2)
    fresh = SafetyController(cfg, CATALOG, docker)    # a new process after a crash
    out = fresh.startup_recovery()
    assert out["outcome"] == "fms-forwarder: started"
    assert fwd(world).running and fresh.latched


def test_startup_recovery_never_touches_containers_outside_the_sandbox(env):
    cfg, world, docker, _, ctl, _ = env
    world.mutate("fms-forwarder", running=False, status="exited", project="someone-else")
    ctl.mark_fault_active("dead-run", "fms-forwarder", "docker_stop")
    out = SafetyController(cfg, CATALOG, docker).startup_recovery()
    assert calls(world, "start") == [] and "state exited" in out["outcome"]
