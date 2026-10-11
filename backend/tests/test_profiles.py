"""Config profiles (GUARDRAIL-DEMO overlays): allowlisted, may only tighten guardrails and response limits."""
from __future__ import annotations

import json
import shutil

import pytest

from resiliencepilot.config import CONFIG_PROFILES, REPO_ROOT, load_experiment_catalog, load_safety_config


def _root(tmp_path, profile=None, safety=None, experiments=None):
    (tmp_path / "config").mkdir()
    for f in ("safety.json", "experiments.json"):
        shutil.copy(REPO_ROOT / "config" / f, tmp_path / "config" / f)
    if profile:
        d = tmp_path / "config" / "profiles" / profile
        d.mkdir(parents=True)
        if safety is not None:
            (d / "safety.json").write_text(json.dumps(safety))
        if experiments is not None:
            (d / "experiments.json").write_text(json.dumps(experiments))
    return tmp_path


def test_default_has_no_profile(monkeypatch):
    monkeypatch.delenv("RP_CONFIG_PROFILE", raising=False)
    assert load_safety_config().profile == "default"
    assert load_experiment_catalog()["dependency_failure"].expected_statuses == (500,)


@pytest.mark.parametrize("name", ["../../etc", "guardrail-demo", "made-up"])
def test_unknown_profile_names_are_refused(monkeypatch, name):
    monkeypatch.setenv("RP_CONFIG_PROFILE", name)
    with pytest.raises(ValueError, match="RP_CONFIG_PROFILE"):
        load_safety_config()


def test_committed_profiles_load_and_change_only_what_they_say(monkeypatch):
    monkeypatch.delenv("RP_CONFIG_PROFILE", raising=False)
    default_catalog = load_experiment_catalog()
    base = default_catalog["dependency_failure"]
    monkeypatch.setenv("RP_CONFIG_PROFILE", "guardrail-demo-error-rate")
    err = load_experiment_catalog()["dependency_failure"]
    assert load_safety_config().profile == "guardrail-demo-error-rate"
    assert err.expected_statuses == () and err.allowed_targets == base.allowed_targets
    assert err.max_duration_s == base.max_duration_s and err.mechanism == base.mechanism
    monkeypatch.setenv("RP_CONFIG_PROFILE", "guardrail-demo-latency")
    lat = load_experiment_catalog()
    assert {k: v for k, v in lat.items() if k != "telemetry_stall"} == {
        k: v for k, v in default_catalog.items() if k != "telemetry_stall"}
    ts, ts0 = lat["telemetry_stall"], default_catalog["telemetry_stall"]
    assert (ts.max_duration_s, ts.fault_observer_timeout_s, ts.fault_latency_p95_max_ms) == (15, 2.0, 30)
    assert (ts.allowed_targets, ts.mechanism, ts.blast_radius) == (ts0.allowed_targets, ts0.mechanism, ts0.blast_radius)
    g = load_safety_config().guardrails
    assert (g.latency_p95_multiplier, g.latency_p95_floor_ms, g.latency_min_samples) == (1.0, 1, 1)
    assert set(CONFIG_PROFILES) == {p.name for p in (REPO_ROOT / "config" / "profiles").iterdir() if p.is_dir()}


@pytest.mark.parametrize("experiments, match", [
    ({"dependency_failure": {"allowed_targets": ["fms-server"]}}, "may not override"),
    ({"dependency_failure": {"max_duration_s": 30}}, "only be tightened"),       # may only shorten (default 15)
    ({"dependency_failure": {"mechanism": "docker_pause"}}, "may not override"),
    ({"dependency_failure": {"blast_radius": ["influxDB", "grafana"]}}, "may not override"),
    ({"network_partition": {"description": "x"}}, "cannot add"),
    ({"dependency_failure": {"fault_observer_timeout_s": 20}}, "fault_observer_timeout_s"),  # bounds still apply
])
def test_profile_cannot_change_targets_durations_or_bounds(tmp_path, monkeypatch, experiments, match):
    monkeypatch.setenv("RP_CONFIG_PROFILE", CONFIG_PROFILES[0])
    root = _root(tmp_path, CONFIG_PROFILES[0], experiments=experiments)
    with pytest.raises(ValueError, match=match):
        load_experiment_catalog(root)


@pytest.mark.parametrize("safety, match", [
    ({"max_duration_s": 30}, "only override 'guardrails'"),
    ({"data_dir": "/tmp/elsewhere"}, "only override 'guardrails'"),
    ({"compose_project": "other"}, "only override 'guardrails'"),
    ({"guardrails": {"latency_p95_multiplier": 50}}, "multiplier"),                # cannot loosen past the bound
    ({"guardrails": {"max_error_rate": 0.9}}, "max_error_rate"),
])
def test_profile_safety_overlay_is_limited_to_bounded_guardrails(tmp_path, monkeypatch, safety, match):
    monkeypatch.setenv("RP_CONFIG_PROFILE", CONFIG_PROFILES[0])
    root = _root(tmp_path, CONFIG_PROFILES[0], safety=safety)
    with pytest.raises(ValueError, match=match):
        load_safety_config(root)


@pytest.mark.parametrize("experiments, match", [
    ({"dependency_failure": {"fault_observer_timeout_s": 8.0, "fault_latency_p95_max_ms": 7600}}, "only be tightened"),
    ({"dependency_failure": {"expected_statuses": [500, 503]}}, "only be removed"),
    ({"dependency_failure": {"max_error_rate": 0.2}}, "only be tightened"),
    ({"service_crash": {"fault_observer_timeout_s": 3.0}}, "only be tightened"),   # implicit default: 2 s
    ({"telemetry_stall": {"max_duration_s": 15, "fault_observer_timeout_s": 2.0, "fault_latency_p95_max_ms": 300}},
     "only be tightened"),                                                        # implicit floor: 250 ms
])
def test_profile_experiment_overrides_only_tighten(tmp_path, monkeypatch, experiments, match):
    monkeypatch.setenv("RP_CONFIG_PROFILE", CONFIG_PROFILES[0])
    with pytest.raises(ValueError, match=match):
        load_experiment_catalog(_root(tmp_path, CONFIG_PROFILES[0], experiments=experiments))


@pytest.mark.parametrize("guardrails, match", [
    ({"max_error_rate": 0.1}, "only be tightened"),
    ({"latency_p95_multiplier": 6.0}, "only be tightened"),
    ({"latency_min_samples": 6}, "only be tightened"),
    ({"latency_window": 30}, "cannot be overridden"),
])
def test_profile_guardrail_overrides_only_tighten(tmp_path, monkeypatch, guardrails, match):
    monkeypatch.setenv("RP_CONFIG_PROFILE", CONFIG_PROFILES[0])
    with pytest.raises(ValueError, match=match):
        load_safety_config(_root(tmp_path, CONFIG_PROFILES[0], safety={"guardrails": guardrails}))


def test_profile_may_add_a_tighter_envelope_than_the_implicit_default(tmp_path, monkeypatch):
    monkeypatch.setenv("RP_CONFIG_PROFILE", CONFIG_PROFILES[1])
    root = _root(tmp_path, CONFIG_PROFILES[1],
                 experiments={"telemetry_stall": {"max_duration_s": 15, "fault_observer_timeout_s": 2.0,
                                                   "fault_latency_p95_max_ms": 30}})
    spec = load_experiment_catalog(root)["telemetry_stall"]
    assert (spec.fault_observer_timeout_s, spec.fault_latency_p95_max_ms) == (2.0, 30)
