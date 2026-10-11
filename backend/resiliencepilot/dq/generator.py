"""Deterministic generator for a dirty, multi-region SYNTHETIC logistics feed (docs/DATA_QUALITY.md).

`count` records, exactly `error_count` of them invalid (each checked against the normalizer at generation time).
Valid records are still "dirty": mixed regional date formats, epoch seconds/milliseconds, padded or lower-case VINs,
lower-case enum codes, numeric strings and nulls. Same seed + same `now` -> same file.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from .normalizer import REGION_ZONES, normalize_record, to_canonical
from .schema import QualityGrade, Region, TruckStatus

# The blueprint's simulated vehicle VIN (recorded in config/ and docs/recon.md) plus synthetic fleet VINs.
VINS = ["YV2E4C3A5VB180691", "PDC312342D5DCA611", "WDB9634031L738214", "1FUJGLDR7CLBP8834", "MAT448174KWB10529"]

ERROR_KINDS = ["AMBIGUOUS_DATE_FORMAT", "TIMESTAMP_IN_FUTURE", "INVALID_VIN", "INVALID_DATE",
               "TEMPERATURE_OUT_OF_RANGE", "INVALID_QUALITY_GRADE", "FORMAT_REGION_MISMATCH", "UNPARSEABLE_TIMESTAMP"]


def _timestamp(rng: random.Random, instant: datetime) -> tuple[Any, str | None]:
    """One of the supported representations of `instant`, with the region it needs."""
    kind = rng.randrange(8)
    if kind == 0:
        local = instant.astimezone(REGION_ZONES[Region.EU])
        return f"{local:%d/%m/%Y %H:%M:%S}", rng.choice(["EU", " eu "])
    if kind == 1:
        local = instant.astimezone(REGION_ZONES[Region.US])
        return f"{local:%m/%d/%Y %H:%M:%S}", "US"
    if kind == 2:
        local = instant.astimezone(REGION_ZONES[Region.IN])
        return f"{local:%d-%m-%Y %H:%M:%S}", "IN"
    if kind == 3:
        return to_canonical(instant), rng.choice(["UTC", None])
    if kind == 4:
        return int(instant.timestamp()), rng.choice([None, "UTC", "EU"])
    if kind == 5:
        return int(instant.timestamp() * 1000), rng.choice([None, "US", "IN"])
    if kind == 6:
        return instant.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(timespec="seconds"), "IN"
    return f"  {to_canonical(instant)}  ", None


def _valid_record(rng: random.Random, record_id: str, now: datetime) -> dict:
    instant = now - timedelta(seconds=rng.randint(3600, 48 * 3600), milliseconds=rng.randint(0, 999))
    ts, region = _timestamp(rng, instant)
    vin = rng.choice(VINS)
    vin = rng.choice([vin, vin.lower(), f"  {vin} "])                     # case preserved, whitespace stripped
    temp: Any = round(rng.uniform(-22.0, 8.0), 1)
    temp = rng.choice([temp, temp, f" {temp} ", None])                    # null stays null
    return {
        "record_id": record_id, "vin": vin, "timestamp_raw": ts, "region": region,
        "cargo_temperature_c": temp,
        "quality_grade": rng.choice([g.value for g in QualityGrade] + ["b", None]),
        "shelf_life_hours": rng.choice([round(rng.uniform(12, 720), 1), None]),
        "truck_status": rng.choice([s.value for s in TruckStatus] + ["in_transit"]),
    }


def _break(rec: dict, kind: str, now: datetime) -> dict:
    rec = dict(rec)
    if kind == "AMBIGUOUS_DATE_FORMAT":
        rec.update(timestamp_raw="03/04/2026 08:30:00", region=None)
    elif kind == "TIMESTAMP_IN_FUTURE":
        rec.update(timestamp_raw=to_canonical(now + timedelta(hours=26)), region="UTC")
    elif kind == "INVALID_VIN":
        rec.update(vin="YV2E4C3A5VB18O69")                                 # 16 chars and a letter O
    elif kind == "INVALID_DATE":
        rec.update(timestamp_raw="31/02/2026 10:00:00", region="EU")
    elif kind == "TEMPERATURE_OUT_OF_RANGE":
        rec.update(cargo_temperature_c=212.0)
    elif kind == "INVALID_QUALITY_GRADE":
        rec.update(quality_grade="E")
    elif kind == "FORMAT_REGION_MISMATCH":
        rec.update(timestamp_raw="15-09-2026 09:00:00", region="US")
    elif kind == "UNPARSEABLE_TIMESTAMP":
        rec.update(timestamp_raw="yesterday at noon")
    return rec


def generate_feed(count: int, error_count: int, *, seed: int = 2026, now: datetime | None = None) -> dict:
    if count < 1 or not 0 <= error_count <= count:
        raise ValueError("need count >= 1 and 0 <= error-count <= count")
    now = now or datetime.now(timezone.utc)
    rng = random.Random(seed)
    stamp = f"{now.astimezone(timezone.utc):%Y%m%dT%H%M%S}"
    bad = set(rng.sample(range(count), error_count))
    records, expected = [], []
    for i in range(count):
        rid = f"LOG-{stamp}-s{seed}-{i:04d}"
        rec = _valid_record(rng, rid, now)
        while not normalize_record(rec, now).valid:                     # e.g. a local time inside a DST fold
            rec = _valid_record(rng, rid, now)
        if i in bad:
            kind = ERROR_KINDS[len(expected) % len(ERROR_KINDS)]
            rec = _break(rec, kind, now)
            expected.append({"record_id": rid, "error": kind})
        records.append(rec)
    return {
        "synthetic": True,
        "note": "Synthetic logistics feed for the ResiliencePilot data-quality gate; complements real rFMS telemetry.",
        "generated_at": to_canonical(now), "seed": seed, "count": count,
        "expected_invalid": expected, "records": records,
    }
