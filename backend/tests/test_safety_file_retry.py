"""Bounded retry for Windows file sharing violations in safety.py.

Held briefly  -> the operation succeeds after a few retries.
Held too long -> the original PermissionError propagates and the system fails closed, exactly as before.
"""
import sys
import threading
import time

import pytest

from resiliencepilot import safety
from resiliencepilot.config import ExperimentSpec
from resiliencepilot.evidence import EvidenceStore
from resiliencepilot.orchestrator import ExperimentRunner
from resiliencepilot.safety import Busy, IO_RETRY_DELAYS_S, SafetyController, _retry_io

from helpers import FakeDocker, FakeObserver, FakeWorld, make_config

CATALOG = {"telemetry_stall": ExperimentSpec("telemetry_stall", "docker_pause", ("fms-forwarder",), 30)}
GOOD = {"experiment_type": "telemetry_stall", "target": "fms-forwarder", "duration_s": 1, "approved_by": "tester"}
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="sharing violations on open files are Windows behaviour")


# ---------- the retry policy itself (any OS) ----------
def test_policy_is_5_attempts_20_to_50ms_apart(monkeypatch):
    sleeps, calls = [], []
    monkeypatch.setattr(safety.time, "sleep", lambda s: sleeps.append(s))

    def always_denied():
        calls.append(1)
        raise PermissionError("held")
    with pytest.raises(PermissionError):
        _retry_io(always_denied)
    assert len(calls) == 5
    assert sleeps == list(IO_RETRY_DELAYS_S) and all(0.02 <= s <= 0.05 for s in sleeps)
    assert sum(sleeps) <= 0.15


def test_succeeds_once_the_holder_lets_go(monkeypatch):
    monkeypatch.setattr(safety.time, "sleep", lambda s: None)
    attempts = []

    def denied_twice():
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError("held")
        return "ok"
    assert _retry_io(denied_twice) == "ok" and len(attempts) == 3


def test_other_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(safety.time, "sleep", lambda s: pytest.fail("must not sleep"))
    calls = []

    def missing():
        calls.append(1)
        raise FileNotFoundError("gone")
    with pytest.raises(FileNotFoundError):
        _retry_io(missing)
    assert len(calls) == 1


def test_lock_acquisition_is_not_retried(tmp_path, monkeypatch):
    """The O_EXCL create is the one-experiment lock; it must stay a single atomic attempt."""
    ctl = SafetyController(make_config(tmp_path), CATALOG, FakeDocker(FakeWorld()))
    real_open, excl_calls = safety.os.open, []

    def denied_open(path, flags, *a):
        if flags & safety.os.O_EXCL:
            excl_calls.append(path)
            raise PermissionError("held")
        return real_open(path, flags, *a)
    monkeypatch.setattr(safety.os, "open", denied_open)
    with pytest.raises(PermissionError):
        ctl.begin("r1")
    assert len(excl_calls) == 1
    assert ctl.status()["active_run"] is None


# ---------- real held handles (Windows) ----------
def hold(path, seconds):
    """Open the file (no FILE_SHARE_DELETE on Windows) and keep it open for `seconds` in a thread."""
    ready = threading.Event()

    def run():
        with open(path, "rb"):
            ready.set()
            time.sleep(seconds)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    ready.wait(2)
    return t


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path)
    world = FakeWorld()
    docker = FakeDocker(world)
    ctl = SafetyController(cfg, CATALOG, docker)
    runner = ExperimentRunner(cfg, CATALOG, ctl, docker, FakeObserver(world), EvidenceStore(cfg.data_dir))
    return cfg, world, ctl, runner


@windows_only
def test_marker_delete_held_briefly_succeeds(env):
    cfg, _, ctl, _ = env
    ctl.mark_fault_active("r", "fms-forwarder", "docker_pause")
    t = hold(cfg.data_dir / "active_fault.json", 0.03)
    ctl.clear_fault_marker()
    assert ctl.active_fault_marker() is None
    t.join()


@windows_only
def test_marker_delete_held_too_long_fails_closed(env):
    cfg, _, ctl, _ = env
    ctl.mark_fault_active("r", "fms-forwarder", "docker_pause")
    t = hold(cfg.data_dir / "active_fault.json", 1.0)
    with pytest.raises(PermissionError):
        ctl.clear_fault_marker()
    t.join()
    assert ctl.active_fault_marker() is not None, "marker must survive: begin() keeps refusing until reset"


@windows_only
def test_latch_write_held_briefly_succeeds(env):
    cfg, _, ctl, _ = env
    ctl.latch("first")
    t = hold(cfg.data_dir / "safety_state.json", 0.03)
    ctl.latch("second")
    t.join()
    assert ctl.latched


@windows_only
def test_run_lock_release_held_briefly_succeeds(env):
    cfg, _, ctl, _ = env
    ctl.begin("r1")
    t = hold(cfg.data_dir / "run.lock", 0.03)
    ctl.end("r1")
    t.join()
    assert not (cfg.data_dir / "run.lock").exists() and ctl.status()["state"] == "READY"


@windows_only
def test_run_lock_release_held_too_long_fails_closed(env):
    cfg, _, ctl, _ = env
    ctl.begin("r1")
    t = hold(cfg.data_dir / "run.lock", 1.0)
    with pytest.raises(PermissionError):
        ctl.end("r1")
    t.join()
    with pytest.raises(Busy):              # still locked: no second experiment can start
        SafetyController(cfg, CATALOG, ctl.docker).begin("r2")


@windows_only
def test_full_run_marker_held_briefly_completes(env):
    cfg, world, ctl, runner = env
    orig = ctl.clear_fault_marker

    def held_then_clear():
        t = hold(cfg.data_dir / "active_fault.json", 0.03)
        orig()
        t.join()
    ctl.clear_fault_marker = held_then_clear
    rec = runner.run(GOOD)
    assert rec["state"] == "COMPLETED" and rec["recovery"]["verified"]


@windows_only
def test_full_run_marker_held_too_long_latches(env):
    cfg, world, ctl, runner = env
    orig = ctl.clear_fault_marker

    def held_too_long():
        t = hold(cfg.data_dir / "active_fault.json", 1.0)
        try:
            orig()
        finally:
            t.join()
    ctl.clear_fault_marker = held_too_long
    rec = runner.run(GOOD)
    assert rec["state"] == "LATCHED_UNSAFE" and ctl.latched      # fail closed, as before the change
    assert not world.forwarder_paused()                            # rollback itself was not affected


@windows_only
def test_latch_write_held_too_long_raises_and_state_stays_unsafe(env):
    """Fail closed: the write error propagates (callers treat it as unsafe); nothing reports 'unlatched'."""
    cfg, _, ctl, _ = env
    ctl.latch("first")
    t = hold(cfg.data_dir / "safety_state.json", 1.0)
    with pytest.raises(PermissionError):
        ctl.latch("second")
    assert ctl.latched                     # read during the hold: retries fail -> treated as latched
    t.join()
    assert ctl.latched                     # after the hold: the earlier latch is still on disk
