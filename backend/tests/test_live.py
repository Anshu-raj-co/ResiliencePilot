"""Read-only checks against the running sandbox. Run with RP_LIVE=1 after scripts/stack-up.sh."""
import time

import pytest

from resiliencepilot.config import load_experiment_catalog, load_safety_config
from resiliencepilot.docker_cli import DockerCLI
from resiliencepilot.safety import RequestRejected, SafetyController
from resiliencepilot.sdv_adapter import RfmsObserver
from resiliencepilot.telemetry import Freshness, FreshnessTracker

pytestmark = pytest.mark.live


def test_rfms_api_serves_advancing_fresh_telemetry():
    cfg = load_safety_config()
    obs = RfmsObserver(cfg.rfms_base_url, cfg.http_timeout_s)
    tracker = FreshnessTracker(cfg.stale_threshold_s)
    results = []
    for _ in range(6):
        results.append(tracker.update(obs.observe()))
        time.sleep(0.5)
    assert all(r.freshness is Freshness.FRESH for r in results)
    assert any(r.advanced for r in results), "createdDateTime must advance within 3 s"


def test_allowlisted_target_is_in_sandbox_project_and_clean():
    cfg = load_safety_config()
    ctl = SafetyController(cfg, load_experiment_catalog(), DockerCLI())
    st = ctl.verify_target("fms-forwarder")
    assert st.project == "resiliencepilot-sdv"


def test_non_allowlisted_real_container_is_rejected():
    cfg = load_safety_config()
    ctl = SafetyController(cfg, load_experiment_catalog(), DockerCLI())
    with pytest.raises(RequestRejected):
        ctl.validate({"experiment_type": "telemetry_stall", "target": "influxDB", "duration_s": 5, "approved_by": "x"})
