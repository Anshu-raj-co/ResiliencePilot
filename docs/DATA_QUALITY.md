# Data Quality Gate

`backend/resiliencepilot/dq/`: a deterministic normalization and quarantine gate for a **synthetic logistics feed**.
Tests: `backend/tests/test_dq.py` (79). Sample report from a real run of the gate: `docs/dq/report.html`.

## Scope and honesty note

- **The logistics fields are synthetic.** The Eclipse SDV Fleet Management Blueprint's rFMS API provides vehicle
  status (VIN, `createdDateTime`, signals), not cargo temperature, quality grade, shelf life or a regional date
  string. We generate those fields (`generate`) to complement the real rFMS telemetry and to show how
  ResiliencePilot treats dirty multi-region input. The VINs include the blueprint's simulated vehicle VIN.
- The gate is **isolated**. It has its own SQLite store, needs no Docker and no live stack, and does not touch the
  safety controller, observer, orchestrator or the blueprint's InfluxDB path. It is not wired into the experiment loop.
- "Before storage" means before *its own* store: a record reaches `dq_clean_records` only after it passes every check.

## Universal format

Every accepted timestamp is stored as canonical UTC ISO-8601 with milliseconds:

```
YYYY-MM-DDTHH:MM:SS.sssZ        e.g. 2026-04-03T08:15:00.000Z
```

Fractions are **truncated** to milliseconds, never rounded, so a value can't spill into the next second. The original
value is kept in `timestamp_raw` for audit.

## Supported input formats and regional rules

| Input | Rule | Example → canonical |
|---|---|---|
| EU `DD/MM/YYYY[ HH:MM[:SS[.f]]]` | region `EU`; wall time in `Europe/Berlin` (CET/CEST) | `03/04/2026 10:15:00` → `2026-04-03T08:15:00.000Z` |
| US `MM/DD/YYYY[ ...]` | region `US`; wall time in `America/New_York` (EST/EDT) | `03/04/2026 10:15:00` → `2026-03-04T15:15:00.000Z` |
| IN `DD-MM-YYYY[ ...]` | region `IN`; wall time in `Asia/Kolkata` (IST) | `03-04-2026 10:15:00` → `2026-04-03T04:45:00.000Z` |
| ISO-8601 with offset or `Z` | any region; the explicit offset wins | `2026-04-03T10:15:00+05:30` → `2026-04-03T04:45:00.000Z` |
| ISO-8601 without offset | uses the record's region zone (`UTC` → UTC) | `2026-04-03 10:15:00` + `UTC` → `…T10:15:00.000Z` |
| Unix epoch seconds | number or numeric string; always UTC | `1775211300` → `2026-04-03T10:15:00.000Z` |
| Unix epoch milliseconds | value ≥ 10^11 is read as ms; always UTC | `1775211300123` → `…T10:15:00.123Z` |

A regional string may also carry an explicit offset (`03/04/2026 10:15 +00:00`), which then beats the region zone.
The region → zone table (`REGION_ZONES` in `normalizer.py`) is a fixed convention of the synthetic feed. On Windows
the IANA zone data comes from the `tzdata` package (declared in `pyproject.toml`).

### Never guessed: reason codes

| Code | When |
|---|---|
| `AMBIGUOUS_DATE_FORMAT` | `DD/MM`/`MM/DD`/`DD-MM` date without a region (`03/04/2026`: 3 April or 4 March?). Quarantined, never guessed, even when the day > 12, because the time zone is unknown too |
| `FORMAT_REGION_MISMATCH` | format does not belong to the region (slash date with `IN`/`UTC`, `DD-MM-YYYY` with `EU`/`US`) |
| `MISSING_TIMEZONE` | ISO-8601 without offset and no region |
| `NONEXISTENT_LOCAL_TIME` / `AMBIGUOUS_LOCAL_TIME` | local time skipped or repeated by a DST change in the region's zone |
| `INVALID_DATE` | calendar-invalid (`31/02/2026`, month 13) |
| `UNPARSEABLE_TIMESTAMP` | no supported format (also booleans, NaN, epochs ≥ 10^14) |
| `MISSING_TIMESTAMP` | `null` or empty |
| `TIMESTAMP_IN_FUTURE` | more than **5.0 s** ahead of system UTC at ingest (≤ 5.0 s is accepted as clock skew) |

## Field rules

| Field | Rule | Codes |
|---|---|---|
| `record_id` | required string; whitespace trimmed, case kept; unique across the batch and the store | `MISSING_RECORD_ID`, `INVALID_RECORD_ID`, `DUPLICATE_RECORD_ID` |
| `vin` | required; whitespace trimmed, **case preserved**; 17 chars of A–Z (no I/O/Q) and 0–9, checked case-insensitively | `MISSING_VIN`, `INVALID_VIN` |
| `region` | `EU`, `US`, `IN`, `UTC` or `null`; trimmed, matched case-insensitively, stored upper-case. An invalid region skips the timestamp parse (it would be a guess) | `INVALID_REGION` |
| `cargo_temperature_c` | number or numeric string, −50…60 °C, or `null` | `INVALID_NUMBER`, `TEMPERATURE_OUT_OF_RANGE` |
| `quality_grade` | `A`–`D` or `null` | `INVALID_QUALITY_GRADE` |
| `shelf_life_hours` | number, 0…8760, or `null` | `INVALID_NUMBER`, `SHELF_LIFE_OUT_OF_RANGE` |
| `truck_status` | `IN_TRANSIT`, `IDLE`, `MAINTENANCE`, `FAULT` or `null` | `INVALID_TRUCK_STATUS` |
| any other key | rejected | `UNKNOWN_FIELD` |
| `timestamp_utc`, `validation_status` | set by the gate (`VALID` / `QUARANTINED`), never taken from input | |

**Nulls:** a missing value stays `null`, never `0`. An empty string is not a null: in a numeric field it is
`INVALID_NUMBER`. Every error in a record is reported, not just the first.

## The batch policy: "2 errors out of 100: roll back or process?"

Threshold: `--threshold`, default **0.05** (5 %), allowed 0.0…0.5 (cannot be loosened further).

| Error rate | Outcome (one atomic SQLite transaction, `BEGIN IMMEDIATE`) | Status / event |
|---|---|---|
| 0 | commit all | `COMMITTED_CLEAN` / `BATCH_COMMITTED` |
| ≤ threshold (e.g. 2/100) | commit the 98 valid records to `dq_clean_records`, write the 2 invalid ones with reasons to `dq_quarantine` | `COMMITTED_WITH_QUARANTINE` / `BATCH_COMMITTED_WITH_QUARANTINE` |
| > threshold (e.g. 10/100) | roll back the whole batch: **0** clean rows, **0** quarantine rows; one audit row with every rejected record's reasons, `needs_operator_review = 1`; CLI exits 2 | `ROLLED_BACK` / `BATCH_ROLLBACK_EXCEEDED_ERROR_THRESHOLD` |

Why: a few bad records in a healthy feed are isolated and reviewed without blocking 98 good ones. A high error rate
means the feed itself is broken (wrong region config, clock fault, schema change), so committing *any* of it could
spread a systematic error, and a human has to look first. Exactly at the threshold (5/100) commits. If any write
fails, the transaction rolls back and nothing is stored, audit row included (tested). Duplicate record ids, whether
within a batch or already committed, are quarantined and never resolved by picking one, so replaying a feed is rejected.

## Store

`backend/data/dq/data_quality.db` (gitignored; `--db` to override):

- `dq_clean_records`: canonical records (`validation_status = 'VALID'` enforced by a CHECK)
- `dq_quarantine`: batch, record id, error codes, error details JSON, the original raw record
- `dq_batch_audit`: one row per batch: event, status, counts, error rate, threshold, operator-review flag, details

## CLI

```bash
cd backend
.venv/bin/python -m resiliencepilot.dq generate --count 100 --error-count 2 --out ../reports/generated/feed.json
.venv/bin/python -m resiliencepilot.dq ingest --file ../reports/generated/feed.json        # exit 0
.venv/bin/python -m resiliencepilot.dq generate --count 100 --error-count 10 --seed 7 --out ../reports/generated/feed10.json
.venv/bin/python -m resiliencepilot.dq ingest --file ../reports/generated/feed10.json      # exit 2: rolled back
.venv/bin/python -m resiliencepilot.dq show-quarantine
.venv/bin/python -m resiliencepilot.dq report --out ../docs/dq/report.html
```

The generator is deterministic (same seed and clock give the same file) and checks every record against the normalizer,
so `--error-count N` gives exactly N invalid records. It cycles through 8 error kinds. Valid records are still dirty:
mixed formats, padded or lower-case VINs, lower-case codes, numeric strings and nulls.
Exit codes: 0 ok, 1 bad input, 2 batch rolled back.

The committed `docs/dq/report.html` came from exactly this sequence (2-of-100 then 10-of-100) on 2026-10-11 against a
scratch database.
