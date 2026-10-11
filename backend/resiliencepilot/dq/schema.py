"""Data-quality record models (docs/DATA_QUALITY.md).

SYNTHETIC DATA NOTICE: every logistics field here (cargo temperature, quality grade, shelf life, truck status, region,
record id) is synthetic. The Eclipse SDV Fleet Management Blueprint's rFMS API does not provide them. They complement
the real rFMS telemetry (VIN, createdDateTime) that the rest of ResiliencePilot observes, so the data-quality gate can
be demonstrated on a dirty multi-region feed. Nothing in this package touches the live stack or the safety controller.
"""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

CANONICAL_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class Region(StrEnum):
    EU = "EU"
    US = "US"
    IN = "IN"
    UTC = "UTC"


class QualityGrade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class TruckStatus(StrEnum):
    IN_TRANSIT = "IN_TRANSIT"
    IDLE = "IDLE"
    MAINTENANCE = "MAINTENANCE"
    FAULT = "FAULT"


class ValidationStatus(StrEnum):
    VALID = "VALID"
    QUARANTINED = "QUARANTINED"


class RawLogisticsRecord(BaseModel):
    """One record as it arrives from the (synthetic) feed: nothing is trusted, every field may be missing or malformed.

    Values are kept exactly as received (no coercion); the normalizer decides what is valid. Unknown fields are kept
    in `extra` so the quarantine store can show the whole record.
    """
    model_config = ConfigDict(extra="allow", frozen=True)

    record_id: Any = None
    vin: Any = None
    timestamp_raw: Any = None
    region: Any = None
    cargo_temperature_c: Any = None
    quality_grade: Any = None
    shelf_life_hours: Any = None
    truck_status: Any = None


class CleanLogisticsRecord(BaseModel):
    """A record that passed every check. Strict: unknown fields rejected, `null` stays `null` (never 0)."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    record_id: str = Field(min_length=1)
    vin: str = Field(min_length=17, max_length=17)          # case preserved as received
    timestamp_raw: str                                      # the original value, as text, for audit
    timestamp_utc: str                                      # canonical YYYY-MM-DDTHH:MM:SS.sssZ
    region: Region | None
    cargo_temperature_c: float | None
    quality_grade: QualityGrade | None
    shelf_life_hours: float | None
    truck_status: TruckStatus | None
    validation_status: ValidationStatus = ValidationStatus.VALID

    @field_validator("timestamp_utc")
    @classmethod
    def _canonical(cls, v: str) -> str:
        if not CANONICAL_UTC_RE.match(v):
            raise ValueError(f"timestamp_utc not canonical UTC ISO-8601: {v!r}")
        return v

    @field_validator("validation_status")
    @classmethod
    def _valid_only(cls, v: ValidationStatus) -> ValidationStatus:
        if v is not ValidationStatus.VALID:
            raise ValueError("a clean record is always VALID")
        return v


class DQError(BaseModel):
    """One reason a record was quarantined. `code` is stable (docs/DATA_QUALITY.md lists them)."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    field: str
    detail: str
