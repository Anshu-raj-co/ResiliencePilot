from resiliencepilot.baseline import collect_baseline, summarise
from resiliencepilot.observation import parse_rfc3339
from resiliencepilot.sdv_adapter import parse_vehicle_statuses
from resiliencepilot.telemetry import Freshness, FreshnessTracker

from helpers import obs


def test_parse_rfc3339_handles_nanoseconds_and_millis():
    a = parse_rfc3339("2026-10-10T08:37:17.942815861Z")
    b = parse_rfc3339("2026-10-10T08:37:16.739Z")
    assert a.microsecond == 942815 and b.microsecond == 739000
    assert (a - b).total_seconds() == 1.203815
    assert parse_rfc3339(None) is None and parse_rfc3339("") is None


def test_parse_real_rfms_body():
    body = {  # captured from the live stack on 2026-10-10
        "moreDataAvailable": False,
        "requestServerDateTime": "2026-10-10T08:37:17.942815861Z",
        "vehicleStatusResponse": {"vehicleStatuses": [{
            "createdDateTime": "2026-10-10T08:37:16.739Z", "receivedDateTime": "2026-10-10T08:37:17.942798935Z",
            "triggerType": {"context": "RFMS", "triggerType": "TIMER"}, "vin": "YV2E4C3A5VB180691"}]},
    }
    o = parse_vehicle_statuses(body, 200, 4.2)
    assert o.vin == "YV2E4C3A5VB180691"
    assert round(o.data_age_s, 3) == 1.204


def test_empty_status_list_is_no_data_not_fresh():
    o = parse_vehicle_statuses({"vehicleStatusResponse": {"vehicleStatuses": []}, "requestServerDateTime": None}, 200, 1.0)
    assert o.created_at is None and o.error
    assert FreshnessTracker(5).update(o).freshness is Freshness.NO_DATA


def test_advancing_telemetry_is_fresh():
    tr = FreshnessTracker(stale_threshold_s=5)
    results = [tr.update(obs(t, t - 1)) for t in range(10)]
    assert all(r.freshness is Freshness.FRESH for r in results)
    assert all(r.advanced for r in results[1:])


def test_http_200_with_frozen_timestamp_is_healthy_but_stale():
    """The core hidden failure: API healthy, receivedDateTime fresh, createdDateTime frozen."""
    tr = FreshnessTracker(stale_threshold_s=5)
    results = [tr.update(obs(t, 0.0)) for t in range(0, 12)]
    assert results[3].freshness is Freshness.FRESH
    assert results[6].freshness is Freshness.STALE
    assert results[6].healthy_but_stale
    assert all(o.http_ok for o in [obs(t, 0.0) for t in range(12)])


def test_progression_detects_stall_even_if_server_clock_wrong():
    # server_time pinned so age looks tiny; only the progression signal can catch it
    tr = FreshnessTracker(stale_threshold_s=2)
    results = [tr.update(obs(t, 10.0, server=10.5)) for t in range(10, 16)]
    assert results[-1].data_age_s == 0.5
    assert results[-1].freshness is Freshness.STALE


def test_old_data_detected_by_age_on_first_sample():
    assert FreshnessTracker(5).update(obs(100, 10)).freshness is Freshness.STALE


def test_observer_error_and_non_200_are_not_freshness_evidence():
    tr = FreshnessTracker(5)
    assert tr.update(obs(0, None, status=None)).freshness is Freshness.OBSERVER_ERROR
    assert tr.update(obs(1, None, status=500)).freshness is Freshness.OBSERVER_ERROR


def test_regression_flagged():
    tr = FreshnessTracker(5)
    tr.update(obs(0, 10))
    assert tr.update(obs(1, 9)).regressed


def test_baseline_summary_reports_unavailable_signals():
    o = [obs(t * 0.5, t * 0.5 - 0.8) for t in range(20)]
    s = summarise(o, 5.0)
    assert s["samples"] == 20 and s["http_ok_ratio"] == 1.0
    assert s["freshness_counts"]["FRESH"] == 20
    assert s["created_advance_interval_s"]["median"] == 0.5
    assert "sequence_id" in s["unavailable_signals"]


def test_collect_baseline_uses_observer():
    class O:
        n = 0
        def observe(self):
            self.n += 1
            return obs(self.n, self.n - 1)
    summary, observations = collect_baseline(O(), samples=5, interval_s=0, stale_threshold_s=5, sleep=lambda s: None)
    assert len(observations) == 5 and summary["samples"] == 5
