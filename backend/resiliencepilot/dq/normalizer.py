"""Deterministic normalizer for the synthetic logistics feed (docs/DATA_QUALITY.md).

Same input + same `now` -> same output. Nothing is guessed: an ambiguous date, a naive time without a region, or a
local time that does not exist (DST gap) or exists twice (DST fold) is quarantined with a reason code instead.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from .schema import (CleanLogisticsRecord, DQError, QualityGrade, Region, RawLogisticsRecord, TruckStatus,
                     ValidationStatus)

FUTURE_TOLERANCE_S = 5.0

# Region -> IANA zone used for naive (offset-less) regional timestamps. Fixed and documented; DST-aware.
REGION_ZONES: dict[Region, ZoneInfo] = {
    Region.EU: ZoneInfo("Europe/Berlin"),
    Region.US: ZoneInfo("America/New_York"),
    Region.IN: ZoneInfo("Asia/Kolkata"),
    Region.UTC: ZoneInfo("UTC"),
}

# Plausibility bounds for synthetic cargo fields.
TEMPERATURE_RANGE_C = (-50.0, 60.0)
SHELF_LIFE_RANGE_H = (0.0, 8760.0)

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)   # ISO 3779 alphabet (no I, O, Q); case preserved
_TIME = r"(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?\s*(Z|[+-]\d{2}:?\d{2})?"
SLASH_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})" + _TIME + r"$")    # EU DD/MM/YYYY or US MM/DD/YYYY
DASH_DMY_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})" + _TIME + r"$")  # IN DD-MM-YYYY
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?)?(Z|[+-]\d{2}:?\d{2})?$")
EPOCH_RE = re.compile(r"^\d{1,14}(\.\d{1,9})?$")
EPOCH_MS_MIN = Decimal(10) ** 11      # >= 1e11 is milliseconds (1e11 s would be year 5138)
EPOCH_MS_MAX = Decimal(10) ** 14

KNOWN_FIELDS = frozenset(RawLogisticsRecord.model_fields)


@dataclass(frozen=True)
class NormalizationResult:
    record_id: str | None
    raw: dict[str, Any]
    clean: CleanLogisticsRecord | None
    errors: tuple[DQError, ...] = field(default_factory=tuple)

    @property
    def valid(self) -> bool:
        return self.clean is not None

    @property
    def status(self) -> ValidationStatus:
        return ValidationStatus.VALID if self.valid else ValidationStatus.QUARANTINED


class TimestampError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


def to_canonical(dt: datetime) -> str:
    """UTC ISO-8601 with milliseconds (truncated, never rounded up into the next second) and a `Z` suffix."""
    dt = dt.astimezone(timezone.utc)
    return f"{dt:%Y-%m-%dT%H:%M:%S}.{dt.microsecond // 1000:03d}Z"


def _offset(text: str | None) -> timezone | None:
    if text is None:
        return None
    if text == "Z":
        return timezone.utc
    sign = -1 if text[0] == "-" else 1
    digits = text[1:].replace(":", "")
    hours, minutes = int(digits[:2]), int(digits[2:])
    if hours > 23 or minutes > 59:
        raise TimestampError("UNPARSEABLE_TIMESTAMP", f"invalid UTC offset {text!r}")
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _localize(naive: datetime, zone: ZoneInfo) -> datetime:
    """Attach `zone` to a naive wall time; refuse times that are skipped or repeated by a DST change."""
    early, late = naive.replace(tzinfo=zone, fold=0), naive.replace(tzinfo=zone, fold=1)
    if early.utcoffset() != late.utcoffset():
        # Either the wall time happens twice (fold) or never (gap). A round trip through UTC tells which.
        round_trip = early.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if round_trip != naive:
            raise TimestampError("NONEXISTENT_LOCAL_TIME", f"{naive} does not exist in {zone.key} (DST gap)")
        raise TimestampError("AMBIGUOUS_LOCAL_TIME", f"{naive} happens twice in {zone.key} (DST fold)")
    return early


def _build(year: int, month: int, day: int, groups: tuple, region: Region | None, raw: str) -> datetime:
    hh, mm, ss, frac, off = groups
    try:
        naive = datetime(year, month, day, int(hh or 0), int(mm or 0), int(ss or 0),
                         int((frac or "0").ljust(6, "0")))
    except ValueError as e:
        raise TimestampError("INVALID_DATE", f"{raw!r}: {e}") from None
    tz = _offset(off)
    if tz is not None:
        return naive.replace(tzinfo=tz)
    if region is None:
        raise TimestampError("MISSING_TIMEZONE", f"{raw!r} has no UTC offset and the record has no region")
    return _localize(naive, REGION_ZONES[region])


def parse_timestamp(raw: Any, region: Region | None) -> datetime:
    """Parse one supported timestamp into an aware UTC datetime, or raise TimestampError with a reason code."""
    if raw is None:
        raise TimestampError("MISSING_TIMESTAMP", "timestamp_raw is null")
    if isinstance(raw, bool):
        raise TimestampError("UNPARSEABLE_TIMESTAMP", f"boolean is not a timestamp: {raw!r}")
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and not math.isfinite(raw):
            raise TimestampError("UNPARSEABLE_TIMESTAMP", f"not a finite number: {raw!r}")
        text = repr(raw) if isinstance(raw, float) else str(raw)
        if not EPOCH_RE.match(text):
            raise TimestampError("UNPARSEABLE_TIMESTAMP", f"unsupported epoch value {raw!r}")
        return _epoch(text)
    if not isinstance(raw, str):
        raise TimestampError("UNPARSEABLE_TIMESTAMP", f"unsupported type {type(raw).__name__}")
    text = raw.strip()
    if not text:
        raise TimestampError("MISSING_TIMESTAMP", "timestamp_raw is empty")

    if EPOCH_RE.match(text):
        return _epoch(text)
    if ISO_RE.match(text):
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as e:
            raise TimestampError("INVALID_DATE", f"{raw!r}: {e}") from None
        if dt.tzinfo is not None:
            return dt
        if region is None:
            raise TimestampError("MISSING_TIMEZONE", f"{raw!r} has no UTC offset and the record has no region")
        return _localize(dt, REGION_ZONES[region])

    if m := SLASH_RE.match(text):
        a, b, year = int(m[1]), int(m[2]), int(m[3])
        if region is None:
            raise TimestampError("AMBIGUOUS_DATE_FORMAT",
                                 f"{raw!r}: slash date without a region (EU DD/MM/YYYY or US MM/DD/YYYY?)")
        if region is Region.EU:
            return _build(year, b, a, m.groups()[3:], region, raw)
        if region is Region.US:
            return _build(year, a, b, m.groups()[3:], region, raw)
        raise TimestampError("FORMAT_REGION_MISMATCH", f"{raw!r}: slash dates are only defined for EU and US, "
                                                       f"not {region.value}")
    if m := DASH_DMY_RE.match(text):
        if region is None:
            raise TimestampError("AMBIGUOUS_DATE_FORMAT", f"{raw!r}: DD-MM-YYYY date without a region")
        if region is not Region.IN:
            raise TimestampError("FORMAT_REGION_MISMATCH", f"{raw!r}: DD-MM-YYYY is the IN format, "
                                                           f"record region is {region.value}")
        return _build(int(m[3]), int(m[2]), int(m[1]), m.groups()[3:], region, raw)
    raise TimestampError("UNPARSEABLE_TIMESTAMP", f"{raw!r} matches no supported format")


def _epoch(text: str) -> datetime:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise TimestampError("UNPARSEABLE_TIMESTAMP", f"bad epoch {text!r}") from None
    if value >= EPOCH_MS_MAX:
        raise TimestampError("UNPARSEABLE_TIMESTAMP", f"epoch {text} too large (only seconds or milliseconds)")
    seconds = value / 1000 if value >= EPOCH_MS_MIN else value
    whole = int(seconds)
    micros = int((seconds - whole) * 1_000_000)
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=whole, microseconds=micros)


def _enum(value: Any, enum: type, field_name: str, code: str, errors: list[DQError]):
    if value is None:
        return None
    if isinstance(value, str) and value.strip().upper() in enum.__members__:
        return enum(value.strip().upper())
    errors.append(DQError(code=code, field=field_name, detail=f"{value!r} not one of {', '.join(enum.__members__)}"))
    return None


def _number(value: Any, field_name: str, bounds: tuple[float, float], range_code: str,
            errors: list[DQError]) -> float | None:
    if value is None:
        return None                                   # null stays null; never 0
    num: float | None = None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        num = float(value)
    elif isinstance(value, str):
        try:
            num = float(value.strip())
        except ValueError:
            num = None
    if num is None or not math.isfinite(num):
        errors.append(DQError(code="INVALID_NUMBER", field=field_name, detail=f"{value!r} is not a finite number"))
        return None
    if not bounds[0] <= num <= bounds[1]:
        errors.append(DQError(code=range_code, field=field_name,
                              detail=f"{num} outside plausible range {bounds[0]}..{bounds[1]}"))
        return None
    return num


def _identifier(value: Any, field_name: str, missing_code: str, invalid_code: str,
                errors: list[DQError]) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        errors.append(DQError(code=missing_code, field=field_name, detail=f"{field_name} is missing"))
        return None
    if not isinstance(value, str):
        errors.append(DQError(code=invalid_code, field=field_name, detail=f"{field_name} must be a string, "
                                                                         f"got {type(value).__name__}"))
        return None
    return value.strip()                              # trim whitespace, keep case


def normalize_record(raw: Any, now: datetime) -> NormalizationResult:
    """Validate and normalize one raw record. `now` must be timezone-aware (the system UTC clock at ingest)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not isinstance(raw, dict):
        err = DQError(code="NOT_AN_OBJECT", field="*", detail=f"record is {type(raw).__name__}, not an object")
        return NormalizationResult(record_id=None, raw={"value": raw}, clean=None, errors=(err,))

    errors: list[DQError] = []
    for key in sorted(set(raw) - KNOWN_FIELDS):
        errors.append(DQError(code="UNKNOWN_FIELD", field=str(key), detail="field not in the logistics schema"))

    record_id = _identifier(raw.get("record_id"), "record_id", "MISSING_RECORD_ID", "INVALID_RECORD_ID", errors)
    vin = _identifier(raw.get("vin"), "vin", "MISSING_VIN", "INVALID_VIN", errors)
    if vin is not None and not VIN_RE.match(vin):
        errors.append(DQError(code="INVALID_VIN", field="vin", detail=f"{vin!r} is not 17 VIN characters "
                                                                      "(A-Z without I/O/Q, 0-9)"))
        vin = None

    region_errors: list[DQError] = []
    region = _enum(raw.get("region"), Region, "region", "INVALID_REGION", region_errors)
    errors.extend(region_errors)

    timestamp_utc: str | None = None
    ts_raw = raw.get("timestamp_raw")
    if not region_errors:                              # an invalid region would make any regional parse a guess
        try:
            dt = parse_timestamp(ts_raw, region)
            ahead = (dt - now).total_seconds()
            if ahead > FUTURE_TOLERANCE_S:
                errors.append(DQError(code="TIMESTAMP_IN_FUTURE", field="timestamp_raw",
                                      detail=f"{to_canonical(dt)} is {ahead:.1f} s ahead of system UTC "
                                             f"{to_canonical(now)} (limit {FUTURE_TOLERANCE_S} s)"))
            else:
                timestamp_utc = to_canonical(dt)
        except TimestampError as e:
            errors.append(DQError(code=e.code, field="timestamp_raw", detail=e.detail))

    temperature = _number(raw.get("cargo_temperature_c"), "cargo_temperature_c", TEMPERATURE_RANGE_C,
                          "TEMPERATURE_OUT_OF_RANGE", errors)
    grade = _enum(raw.get("quality_grade"), QualityGrade, "quality_grade", "INVALID_QUALITY_GRADE", errors)
    shelf = _number(raw.get("shelf_life_hours"), "shelf_life_hours", SHELF_LIFE_RANGE_H,
                    "SHELF_LIFE_OUT_OF_RANGE", errors)
    status = _enum(raw.get("truck_status"), TruckStatus, "truck_status", "INVALID_TRUCK_STATUS", errors)

    if errors:
        return NormalizationResult(record_id=record_id, raw=dict(raw), clean=None, errors=tuple(errors))
    assert record_id is not None and vin is not None and timestamp_utc is not None
    clean = CleanLogisticsRecord(
        record_id=record_id, vin=vin,
        timestamp_raw=ts_raw.strip() if isinstance(ts_raw, str) else str(ts_raw),
        timestamp_utc=timestamp_utc, region=region, cargo_temperature_c=temperature, quality_grade=grade,
        shelf_life_hours=shelf, truck_status=status)
    return NormalizationResult(record_id=record_id, raw=dict(raw), clean=clean)
