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
    base = load_experiment_catalog()["dependency_failure"]
    monkeypatch.setenv("RP_CONFIG_PROFILE", "guardrail-demo-error-rate")
    err = load_experiment_catalog()["dependency_failure"]
    assert load_safety_config().profile == "guardrail-demo-error-rate"
    assert err.expected_statuses == () and err.allowed_targets == base.allowed_targets
    assert err.max_duration_s == base.max_duration_s and err.mechanism == base.mechanism
    monkeypatch.setenv("RP_CONFIG_PROFILE", "guardrail-demo-latency")
    lat = load_experiment_catalog()["dependency_failure"]
    assert lat.fault_latency_p95_max_ms == 1000 and lat.expected_statuses == (500,)
    assert load_safety_config().guardrails.latency_min_samples == 1
    assert set(CONFIG_PROFILES) == {p.name for p in (REPO_ROOT / "config" / "profiles").iterdir() if p.is_dir()}


@pytest.mark.parametrize("experiments, match", [
    ({"dependency_failure": {"allowed_targets": ["fms-server"]}}, "may not override"),
    ({"dependency_failure": {"max_duration_s": 30}}, "may not override"),
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
