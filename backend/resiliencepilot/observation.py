"""Observation record: one poll of the rFMS API."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_rfc3339(value: str | None) -> datetime | None:
    """Parse rFMS timestamps such as '2026-10-10T08:37:17.942815861Z' (nanoseconds truncated to µs)."""
    if not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "." in s:
        head, rest = s.split(".", 1)
        n = 0
        while n < len(rest) and rest[n].isdigit():
            n += 1
        frac, tz = rest[:n], rest[n:]
        s = f"{head}.{frac[:6].ljust(6, '0')}{tz}"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Observation:
    observed_at: datetime
    http_status: int | None
    latency_ms: float | None
    vin: str | None
    created_at: datetime | None
    received_at: datetime | None
    server_time: datetime | None
    error: str | None = None

    @property
    def data_age_s(self) -> float | None:
        """Age of the newest telemetry, measured entirely on the fms-server (Docker VM) clock, so skew-free."""
        if self.created_at is None or self.server_time is None:
            return None
        return (self.server_time - self.created_at).total_seconds()

    @property
    def http_ok(self) -> bool:
        return self.http_status == 200

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        d["data_age_s"] = None if self.data_age_s is None else round(self.data_age_s, 3)
        return d
