"""Baseline collector: repeated observations under normal conditions, summarised honestly."""
from __future__ import annotations

import statistics
import time
from typing import Callable, Protocol

from .observation import Observation, utcnow
from .telemetry import Freshness, FreshnessTracker

UNAVAILABLE_SIGNALS = {
    "sequence_id": "VehicleStatus carries no sequence number; progression uses strictly increasing createdDateTime",
    "end_to_end_latency": "createdDateTime is stamped by fms-forwarder, but InfluxDB write time is not exposed by the rFMS API; "
                          "data_age_s (server_time - createdDateTime) is an upper bound on pipeline delay plus polling offset",
    "completeness": "no expected-message counter exists in the data; not measured",
}


class Observer(Protocol):
    def observe(self) -> Observation: ...


def _stats(values: list[float]) -> dict | None:
    if not values:
        return None
    s = sorted(values)
    p95 = s[min(len(s) - 1, round(0.95 * (len(s) - 1)))]
    return {"n": len(s), "min": round(s[0], 3), "median": round(statistics.median(s), 3),
            "p95": round(p95, 3), "max": round(s[-1], 3)}


def summarise(observations: list[Observation], stale_threshold_s: float) -> dict:
    tracker = FreshnessTracker(stale_threshold_s)
    assessments = [tracker.update(o) for o in observations]
    created = [o.created_at for o in observations if o.created_at is not None]
    distinct = sorted(set(created))
    advance_intervals = [(b - a).total_seconds() for a, b in zip(distinct, distinct[1:])]
    counts = {f.value: sum(1 for a in assessments if a.freshness is f) for f in Freshness}
    return {
        "samples": len(observations),
        "started_at": observations[0].observed_at.isoformat() if observations else None,
        "ended_at": observations[-1].observed_at.isoformat() if observations else None,
        "http_ok_ratio": round(sum(o.http_ok for o in observations) / len(observations), 3) if observations else None,
        "vins": sorted({o.vin for o in observations if o.vin}),
        "freshness_counts": counts,
        "healthy_but_stale_count": sum(a.healthy_but_stale for a in assessments),
        "data_age_s": _stats([o.data_age_s for o in observations if o.data_age_s is not None]),
        "created_advance_interval_s": _stats(advance_intervals),
        "distinct_created_values": len(distinct),
        "regressions": sum(a.regressed for a in assessments),
        "latency_ms": _stats([o.latency_ms for o in observations if o.latency_ms is not None]),
        "stale_threshold_s": stale_threshold_s,
        "unavailable_signals": UNAVAILABLE_SIGNALS,
    }


def collect_baseline(observer: Observer, samples: int, interval_s: float, stale_threshold_s: float,
                     sleep: Callable[[float], None] = time.sleep) -> tuple[dict, list[Observation]]:
    observations: list[Observation] = []
    for i in range(samples):
        observations.append(observer.observe())
        if i < samples - 1:
            sleep(interval_s)
    summary = summarise(observations, stale_threshold_s)
    summary["collected_at"] = utcnow().isoformat()
    summary["interval_s"] = interval_s
    return summary, observations
