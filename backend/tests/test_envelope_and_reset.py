"""(b) per-experiment response envelope for slow-failing dependencies, and reset gated on fresh telemetry."""
import time
from datetime import datetime, timezone

import pytest

from resiliencepilot.config import ExperimentSpec, Guardrails, load_experiment_catalog
from resiliencepilot.evidence import EvidenceStore
from resiliencepilot.observation import Observation
from resiliencepilot.orchestrator import ExperimentRunner
from resiliencepilot.safety import SafetyController, SafetyError

from helpers import FakeDocker, FakeObserver, FakeWorld, make_config

DEP_SPEC = ExperimentSpec("dependency_failure", "docker_stop", ("influxDB",), 15, expected_statuses=(500,),
                          fault_observer_timeout_s=2.0, fault_latency_p95_max_ms=1500.0)
CATALOG = {"telemetry_stall": ExperimentSpec("telemetry_stall", "docker_pause", ("fms-forwarder",), 30),
           "dependency_failure": DEP_SPEC}
DEP = {"experiment_type": "dependency_failure", "target": "influxDB", "duration_s": 4, "approved_by": "tester"}


def build(tmp_path, catalog=CATALOG, **cfg_overrides):
    cfg = make_config(tmp_path, **cfg_overrides)
    world = FakeWorld()
    docker = FakeDocker(world)
    obs = FakeObserver(world)
    ctl = SafetyController(cfg, catalog, docker, observer=obs)
    ctl.RESET_TELEMETRY_TIMEOUT_S = 1.0
    runner = ExperimentRunner(cfg, catalog, ctl, docker, obs, EvidenceStore(cfg.data_dir))
    return world, docker, ctl, runner


# ======================= reset requires fresh, advancing telemetry =======================
def test_reset_refused_without_an_observer(tmp_path):
    cfg = make_config(tmp_path)
    ctl = SafetyController(cfg, CATALOG, FakeDocker(FakeWorld()))
    ctl.latch("x")
    with pytest.raises(SafetyError, match="telemetry observer"):
        ctl.reset("tester", "no observer")
    assert ctl.latched


def test_reset_refused_while_api_answers_500_with_healthy_containers(tmp_path):
    """The 2026-10-10 incident: every container running, API still failing -> reset must refuse."""
    world, docker, ctl, _ = build(tmp_path)
    ctl.latch("incident")
    world.api_status = 500
    with pytest.raises(SafetyError, match="telemetry not fresh"):
        ctl.reset("tester", "containers look fine")
    assert ctl.latched


def test_reset_refused_while_data_is_frozen_with_http_200(tmp_path):
    world, docker, ctl, _ = build(tmp_path)
    ctl.latch("x")
    world.stay_stale_after_unpause = True
    world.calls.append((0, "unpause", "fms-forwarder"))   # makes the fake serve frozen createdDateTime with HTTP 200
    time.sleep(0.4)                                       # older than the 0.3 s stale threshold
    with pytest.raises(SafetyError, match="telemetry not fresh"):
        ctl.reset("tester", "HTTP 200 is not enough")
    assert ctl.latched


def test_reset_succeeds_with_three_fresh_advancing_polls_and_records_the_check(tmp_path):
    world, docker, ctl, _ = build(tmp_path)
    ctl.latch("x")
    out = ctl.reset("tester", "checked")
    assert not ctl.latched
    assert out["telemetry_check"]["fresh_consecutive"] >= 3 and out["telemetry_check"]["advanced"]
    hist = ctl._read_state()["history"][-1]
    assert hist["event"] == "reset" and hist["telemetry_check"]["fresh_consecutive"] >= 3


def test_reset_rechecks_containers_after_polling(tmp_path):
    world, docker, ctl, _ = build(tmp_path)
    ctl.latch("x")
    calls = {"n": 0}

    class FreshButBreaksNeighbour:
        def observe(self):
            calls["n"] += 1
            if calls["n"] == 2:
                world.mutate("influxDB", running=False, status="exited")   # something stops while we poll
            now = datetime.now(timezone.utc)
            return Observation(now, 200, 3.0, "VIN1", now, now, now)
    ctl.observer = FreshButBreaksNeighbour()
    with pytest.raises(SafetyError, match="targets not clean"):
        ctl.reset("tester", "race")
    assert ctl.latched


def test_reset_needs_an_advance_not_just_three_fresh_polls(tmp_path):
    world, docker, ctl, _ = build(tmp_path)
    ctl.latch("x")
    fixed = datetime.now(timezone.utc)

    class FreshButNeverAdvancing:
        def observe(self):
            now = datetime.now(timezone.utc)
            return Observation(now, 200, 3.0, "VIN1", fixed, now, fixed)   # age 0 on the server clock, never advances
    ctl.observer = FreshButNeverAdvancing()
    with pytest.raises(SafetyError):
        ctl.reset("tester", "no advance")


# ======================= (b) response envelope =======================
@pytest.mark.parametrize("kw", [dict(fault_observer_timeout_s=8.01), dict(fault_observer_timeout_s=0),
                                dict(fault_observer_timeout_s=60.0),
                                dict(fault_latency_p95_max_ms=1000.0),                       # cap without a timeout
                                dict(fault_observer_timeout_s=2.0, fault_latency_p95_max_ms=2000.0),   # >= timeout
                                dict(fault_observer_timeout_s=5.0, fault_latency_p95_max_ms=5001.0),   # >= timeout
                                dict(fault_observer_timeout_s=8.0, fault_latency_p95_max_ms=7501.0),   # above ceiling
                                dict(fault_observer_timeout_s=8.0, fault_latency_p95_max_ms=8000.0)])  # == timeout
def test_envelope_bounds(kw):
    with pytest.raises(ValueError):
        ExperimentSpec("x", "docker_stop", ("influxDB",), 15, **kw)


def test_envelope_cannot_push_total_fault_time_past_30s():
    with pytest.raises(ValueError):
        ExperimentSpec("x", "docker_stop", ("influxDB",), 26, fault_observer_timeout_s=5.0)
    with pytest.raises(ValueError):                    # D-022: the wider 8 s timeout still counts against the 30 s cap
        ExperimentSpec("x", "docker_stop", ("influxDB",), 23, fault_observer_timeout_s=8.0)
    ExperimentSpec("x", "docker_stop", ("influxDB",), 22, fault_observer_timeout_s=8.0, fault_latency_p95_max_ms=7500.0)


def test_d022_ceilings_are_exactly_8s_and_7500ms():
    from resiliencepilot import config
    assert config.MAX_FAULT_OBSERVER_TIMEOUT_S == 8.0 and config.MAX_FAULT_LATENCY_P95_MS == 7500.0
    assert config.MAX_FAULT_LATENCY_P95_MS < config.MAX_FAULT_OBSERVER_TIMEOUT_S * 1000


def test_catalogue_envelope_only_for_dependency_failure():
    cat = load_experiment_catalog()
    d = cat["dependency_failure"]
    assert d.fault_observer_timeout_s == 8.0 and d.fault_latency_p95_max_ms == 7500.0
    assert d.max_duration_s + d.fault_observer_timeout_s <= 30
    assert cat["telemetry_stall"].fault_observer_timeout_s is None and cat["service_crash"].fault_observer_timeout_s is None


def test_slow_500s_inside_the_envelope_are_observed_for_the_full_window(tmp_path):
    world, docker, ctl, runner = build(tmp_path)
    world.influx_down_delay_s = 1.0                    # slower than the global 0.5 s timeout, inside the 2 s envelope
    rec = runner.run(DEP)
    assert rec["stop_reason"] == "duration_elapsed" and rec["state"] == "COMPLETED"
    assert rec["impact"]["expected_status_observations"] >= 2
    assert rec["guardrails"]["fault_observer_timeout_s"] == 2.0
    assert rec["guardrails"]["latency_threshold_ms"] == 1500.0
    assert rec["guardrails"]["latency_threshold_source"].startswith("experiment envelope")
    assert rec["recovery"]["verified"]


def test_same_slowness_without_an_envelope_aborts(tmp_path):
    spec = ExperimentSpec("dependency_failure", "docker_stop", ("influxDB",), 15, expected_statuses=(500,))
    world, docker, ctl, runner = build(tmp_path, catalog={**CATALOG, "dependency_failure": spec})
    world.influx_down_delay_s = 1.0
    rec = runner.run(DEP)
    assert rec["stop_reason"] == "observer_lost"


def test_response_slower_than_the_envelope_aborts_on_first_poll(tmp_path):
    world, docker, ctl, runner = build(tmp_path)
    world.influx_down_delay_s = 3.0                    # beyond the 2 s envelope: a timeout
    rec = runner.run(DEP)
    assert rec["stop_reason"] == "observer_lost"
    assert sum(1 for o in rec["observations"] if o["phase"] == "fault") == 1
    assert rec["rollback"]["verified"]


def test_latency_cap_inside_the_envelope_still_trips(tmp_path):
    world, docker, ctl, runner = build(tmp_path, guardrails=Guardrails(latency_window=2, latency_min_samples=2))
    world.influx_down_delay_s = 1.8                    # answers (inside 2 s) but p95 above the 1.5 s cap
    rec = runner.run({**DEP, "duration_s": 10})
    assert rec["stop_reason"] == "guardrail_latency"
    assert rec["guardrails"]["triggered"]["detail"]["threshold_ms"] == 1500.0
    assert rec["rollback"]["verified"]


def test_no_poll_starts_that_could_outlive_the_fault_window(tmp_path):
    world, docker, ctl, runner = build(tmp_path)
    world.influx_down_delay_s = 1.0
    rec = runner.run({**DEP, "duration_s": 3})
    inj = next(e["t_s"] for e in rec["timeline"] if e["event"] == "injecting")
    starts = [o["t_s"] - 1.0 - inj for o in rec["observations"] if o["phase"] == "fault"]   # t_s is the answer time
    assert all(s <= 3 - 2.0 + 0.3 for s in starts), starts
    assert rec["stop_reason"] == "duration_elapsed"


def test_envelope_applies_to_fault_polls_only(tmp_path):
    world, docker, ctl, runner = build(tmp_path)
    seen = []
    orig = runner.observer.observe

    def spy(timeout_s=None):
        seen.append((world.influx_down(), timeout_s))
        return orig(timeout_s=timeout_s)
    runner.observer.observe = spy
    runner.run({**DEP, "duration_s": 4})
    assert all(t is None for down, t in seen if not down), "precheck/recovery keep the global timeout"
    assert any(t == 2.0 for down, t in seen if down)


def test_duration_must_exceed_the_envelope_timeout(tmp_path):
    from resiliencepilot.safety import RequestRejected
    world, docker, ctl, runner = build(tmp_path)
    with pytest.raises(RequestRejected, match="must exceed the fault observer timeout"):
        runner.run({**DEP, "duration_s": 2})
    assert world.calls == []
