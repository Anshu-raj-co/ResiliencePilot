"""Telemetry verifier: freshness and progression from createdDateTime. Pure logic, no I/O.

Two independent stale signals:
  * age      - server_time - createdDateTime > stale_threshold_s (fms-server clock on both sides)
  * progress - createdDateTime has not advanced for > stale_threshold_s of local observation time
HTTP 200 and receivedDateTime are deliberately NOT freshness signals.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .observation import Observation


class Freshness(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    NO_DATA = "NO_DATA"                # API answered 200 but carried no usable createdDateTime
    OBSERVER_ERROR = "OBSERVER_ERROR"  # no response or non-200


@dataclass(frozen=True)
class Assessment:
    freshness: Freshness
    data_age_s: float | None
    stalled_for_s: float | None   # local time since createdDateTime last advanced
    advanced: bool                # created_at moved forward vs the previous sample
    regressed: bool               # created_at moved backwards (anomaly, recorded)
    healthy_but_stale: bool       # HTTP 200 while telemetry is stale: the hidden failure

    def to_dict(self) -> dict:
        return {
            "freshness": self.freshness.value,
            "data_age_s": None if self.data_age_s is None else round(self.data_age_s, 3),
            "stalled_for_s": None if self.stalled_for_s is None else round(self.stalled_for_s, 3),
            "advanced": self.advanced,
            "regressed": self.regressed,
            "healthy_but_stale": self.healthy_but_stale,
        }


class FreshnessTracker:
    """Feed observations in order; returns one Assessment per observation."""

    def __init__(self, stale_threshold_s: float) -> None:
        self.stale_threshold_s = stale_threshold_s
        self.last_created: datetime | None = None
        self.last_advance_at: datetime | None = None

    def update(self, obs: Observation) -> Assessment:
        if not obs.http_ok:
            return Assessment(Freshness.OBSERVER_ERROR, None, self._stalled_for(obs), False, False, False)
        if obs.created_at is None:
            return Assessment(Freshness.NO_DATA, None, self._stalled_for(obs), False, False, False)

        advanced = regressed = False
        if self.last_created is None or obs.created_at > self.last_created:
            advanced = self.last_created is not None
            self.last_created = obs.created_at
            self.last_advance_at = obs.observed_at
        elif obs.created_at < self.last_created:
            regressed = True

        age = obs.data_age_s
        stalled = self._stalled_for(obs)
        stale = (age is not None and age > self.stale_threshold_s) or (
            stalled is not None and stalled > self.stale_threshold_s
        )
        return Assessment(
            Freshness.STALE if stale else Freshness.FRESH, age, stalled, advanced, regressed,
            healthy_but_stale=stale,  # http_ok is guaranteed on this branch
        )

    def _stalled_for(self, obs: Observation) -> float | None:
        if self.last_advance_at is None:
            return None
        return (obs.observed_at - self.last_advance_at).total_seconds()
