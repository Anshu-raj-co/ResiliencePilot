"""SDV adapter: read-only observer of the blueprint's public rFMS API (fms-server)."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from .observation import Observation, parse_rfc3339, utcnow

VEHICLE_STATUS_PATH = "/rfms/vehiclestatuses"


class RfmsObserver:
    def __init__(self, base_url: str, timeout_s: float, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)

    def observe(self, timeout_s: float | None = None) -> Observation:
        """One poll. `timeout_s` overrides the client timeout for this poll only (fault-window envelope)."""
        start = time.perf_counter()
        try:
            kw = {} if timeout_s is None else {"timeout": timeout_s}
            resp = self._client.get(VEHICLE_STATUS_PATH, params={"latestOnly": "true"}, **kw)
        except httpx.HTTPError as exc:
            return Observation(utcnow(), None, None, None, None, None, None, error=f"{type(exc).__name__}: {exc}")
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        if resp.status_code != 200:
            return Observation(utcnow(), resp.status_code, latency_ms, None, None, None, None, error=f"HTTP {resp.status_code}")
        try:
            return parse_vehicle_statuses(resp.json(), resp.status_code, latency_ms)
        except (ValueError, KeyError, TypeError) as exc:
            return Observation(utcnow(), resp.status_code, latency_ms, None, None, None, None, error=f"unparseable body: {exc}")

    def statuses_between(self, start: datetime, end: datetime) -> list[datetime]:
        """createdDateTime of every status stored between start and end (read-only rFMS window query)."""
        fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        resp = self._client.get(VEHICLE_STATUS_PATH, params={"starttime": fmt(start), "stoptime": fmt(end)})
        resp.raise_for_status()
        statuses = resp.json()["vehicleStatusResponse"]["vehicleStatuses"] or []
        return sorted(c for c in (parse_rfc3339(s.get("createdDateTime")) for s in statuses) if c is not None)

    def close(self) -> None:
        self._client.close()


def parse_vehicle_statuses(body: dict, http_status: int, latency_ms: float | None) -> Observation:
    """Pick the status with the newest createdDateTime. An empty list is a valid 200 that carries no data."""
    statuses = body["vehicleStatusResponse"]["vehicleStatuses"] or []
    server_time = parse_rfc3339(body.get("requestServerDateTime"))
    newest = None
    for s in statuses:
        created = parse_rfc3339(s.get("createdDateTime"))
        if created is not None and (newest is None or created > newest[0]):
            newest = (created, s)
    if newest is None:
        return Observation(utcnow(), http_status, latency_ms, None, None, None, server_time,
                           error="no vehicle status with createdDateTime")
    created, s = newest
    return Observation(utcnow(), http_status, latency_ms, s.get("vin"), created,
                       parse_rfc3339(s.get("receivedDateTime")), server_time)
