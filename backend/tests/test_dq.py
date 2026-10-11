"""Data-quality gate (resiliencepilot.dq): normalizer, batch policy, store, CLI. No Docker, no live stack."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from resiliencepilot.dq import cli
from resiliencepilot.dq.batch_engine import BatchStatus, ingest_batch, validate_threshold
from resiliencepilot.dq.generator import generate_feed
from resiliencepilot.dq.normalizer import TimestampError, normalize_record, parse_timestamp, to_canonical
from resiliencepilot.dq.report import render_report
from resiliencepilot.dq.schema import CleanLogisticsRecord, Region
from resiliencepilot.dq.store import DQStore

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
VIN = "YV2E4C3A5VB180691"


def rec(i=0, **kw):
    base = {"record_id": f"R-{i:04d}", "vin": VIN, "timestamp_raw": "2026-10-10T08:00:00Z", "region": "UTC",
            "cargo_temperature_c": 4.0, "quality_grade": "A", "shelf_life_hours": 72.0, "truck_status": "IN_TRANSIT"}
    base.update(kw)
    return base


def bad(i):
    return rec(i, timestamp_raw="03/04/2026", region=None)


def canon(raw, region):
    return to_canonical(parse_timestamp(raw, None if region is None else Region(region)))


@pytest.fixture
def store(tmp_path):
    return DQStore(tmp_path / "dq.db")


# ---- date normalization ----

@pytest.mark.parametrize("raw,region,expected", [
    ("03/04/2026 10:15:00", "EU", "2026-04-03T08:15:00.000Z"),     # EU DD/MM, CEST (+02:00)
    ("03/04/2026 10:15:00", "US", "2026-03-04T15:15:00.000Z"),     # US MM/DD, EST (-05:00)
    ("03-04-2026 10:15:00", "IN", "2026-04-03T04:45:00.000Z"),     # IN DD-MM, IST (+05:30)
    ("15/01/2026 09:00", "EU", "2026-01-15T08:00:00.000Z"),        # CET (+01:00), no seconds
    ("07/04/2026 08:00:00.5", "US", "2026-07-04T12:00:00.500Z"),   # EDT (-04:00), fraction
    ("2026-04-03T10:15:00Z", None, "2026-04-03T10:15:00.000Z"),
    ("2026-04-03T10:15:00.123456+05:30", None, "2026-04-03T04:45:00.123Z"),   # truncated to ms
    ("2026-04-03 10:15:00", "UTC", "2026-04-03T10:15:00.000Z"),
    ("2026-04-03T10:15:00", "IN", "2026-04-03T04:45:00.000Z"),
    ("03/04/2026 10:15:00 +00:00", "EU", "2026-04-03T10:15:00.000Z"),         # explicit offset beats region zone
    (1775211300, None, "2026-04-03T10:15:00.000Z"),                # epoch seconds (int)
    ("1775211300", None, "2026-04-03T10:15:00.000Z"),              # epoch seconds (string)
    (1775211300123, None, "2026-04-03T10:15:00.123Z"),             # epoch milliseconds
    (1775211300.999, "EU", "2026-04-03T10:15:00.999Z"),            # epoch is always UTC, region ignored
    ("  2026-04-03T10:15:00Z  ", None, "2026-04-03T10:15:00.000Z"),  # whitespace stripped
])
def test_supported_formats_normalize_to_canonical_utc(raw, region, expected):
    assert canon(raw, region) == expected


def test_region_decides_day_month_order():
    assert canon("03/04/2026 12:00:00 Z", "EU")[:10] == "2026-04-03"
    assert canon("03/04/2026 12:00:00 Z", "US")[:10] == "2026-03-04"


@pytest.mark.parametrize("raw", ["03/04/2026", "03/04/2026 10:15:00", "25/04/2026", "03-04-2026"])
def test_regional_date_without_region_is_never_guessed(raw):
    r = normalize_record(rec(timestamp_raw=raw, region=None), NOW)
    assert not r.valid and [e.code for e in r.errors] == ["AMBIGUOUS_DATE_FORMAT"]


@pytest.mark.parametrize("raw,region,code", [
    ("03/04/2026", "IN", "FORMAT_REGION_MISMATCH"),
    ("03/04/2026", "UTC", "FORMAT_REGION_MISMATCH"),
    ("03-04-2026", "EU", "FORMAT_REGION_MISMATCH"),
    ("31/02/2026", "EU", "INVALID_DATE"),
    ("02/31/2026", "US", "INVALID_DATE"),
    ("13/13/2026", "EU", "INVALID_DATE"),
    ("2026-04-03T10:15:00", None, "MISSING_TIMEZONE"),
    ("yesterday", "EU", "UNPARSEABLE_TIMESTAMP"),
    ("2026/04/03", "EU", "UNPARSEABLE_TIMESTAMP"),
    (None, "EU", "MISSING_TIMESTAMP"),
    ("   ", "EU", "MISSING_TIMESTAMP"),
    (True, None, "UNPARSEABLE_TIMESTAMP"),
    (float("nan"), None, "UNPARSEABLE_TIMESTAMP"),
    (10 ** 15, None, "UNPARSEABLE_TIMESTAMP"),                    # microseconds: unsupported, not guessed
    ("29/03/2026 02:30:00", "EU", "NONEXISTENT_LOCAL_TIME"),       # Berlin DST gap
    ("25/10/2026 02:30:00", "EU", "AMBIGUOUS_LOCAL_TIME"),         # Berlin DST fold
])
def test_bad_timestamps_get_a_reason_code(raw, region, code):
    with pytest.raises(TimestampError) as ei:
        parse_timestamp(raw, None if region is None else Region(region))
    assert ei.value.code == code


def test_future_timestamp_rejected_beyond_5_seconds():
    for ahead, ok in [(4.9, True), (5.0, True), (5.1, False), (3600, False)]:
        ts = to_canonical(NOW + timedelta(seconds=ahead))
        r = normalize_record(rec(timestamp_raw=ts), NOW)
        assert r.valid is ok, ahead
        if not ok:
            assert [e.code for e in r.errors] == ["TIMESTAMP_IN_FUTURE"]


def test_normalizer_is_deterministic():
    raw = rec(timestamp_raw="03/04/2026 10:15:00", region="EU")
    assert normalize_record(raw, NOW) == normalize_record(dict(raw), NOW)


def test_now_must_be_aware():
    with pytest.raises(ValueError):
        normalize_record(rec(), datetime(2026, 1, 1))


# ---- casing, whitespace, nulls ----

def test_vin_case_preserved_and_whitespace_stripped():
    r = normalize_record(rec(vin="  yv2E4c3a5vb180691 \t"), NOW)
    assert r.valid and r.clean.vin == "yv2E4c3a5vb180691"


def test_enum_codes_are_trimmed_and_upper_cased():
    r = normalize_record(rec(region=" eu ", timestamp_raw="03/04/2026 10:15:00", quality_grade="b",
                             truck_status="in_transit"), NOW)
    assert r.valid and (r.clean.region, r.clean.quality_grade, r.clean.truck_status) == ("EU", "B", "IN_TRANSIT")


def test_nulls_stay_null_never_zero():
    r = normalize_record(rec(cargo_temperature_c=None, quality_grade=None, shelf_life_hours=None, truck_status=None,
                             region=None), NOW)
    assert r.valid
    c = r.clean
    assert (c.cargo_temperature_c, c.quality_grade, c.shelf_life_hours, c.truck_status, c.region) == (None,) * 5


def test_zero_is_kept_as_zero_and_numeric_strings_parse():
    r = normalize_record(rec(cargo_temperature_c=0, shelf_life_hours=" 12.5 "), NOW)
    assert r.valid and r.clean.cargo_temperature_c == 0.0 and r.clean.shelf_life_hours == 12.5


@pytest.mark.parametrize("override,code", [
    ({"vin": "YV2E4C3A5VB18069"}, "INVALID_VIN"),            # 16 chars
    ({"vin": "YV2E4C3A5VB18O691"}, "INVALID_VIN"),           # letter O
    ({"vin": None}, "MISSING_VIN"),
    ({"vin": "   "}, "MISSING_VIN"),
    ({"vin": 12345678901234567}, "INVALID_VIN"),
    ({"record_id": None}, "MISSING_RECORD_ID"),
    ({"region": "APAC"}, "INVALID_REGION"),
    ({"quality_grade": "E"}, "INVALID_QUALITY_GRADE"),
    ({"truck_status": "PARKED"}, "INVALID_TRUCK_STATUS"),
    ({"cargo_temperature_c": "warm"}, "INVALID_NUMBER"),
    ({"cargo_temperature_c": ""}, "INVALID_NUMBER"),          # empty string is not silently null
    ({"cargo_temperature_c": True}, "INVALID_NUMBER"),
    ({"cargo_temperature_c": 212.0}, "TEMPERATURE_OUT_OF_RANGE"),
    ({"shelf_life_hours": -1}, "SHELF_LIFE_OUT_OF_RANGE"),
    ({"surprise": 1}, "UNKNOWN_FIELD"),
])
def test_field_errors(override, code):
    r = normalize_record(rec(**override), NOW)
    assert not r.valid and code in [e.code for e in r.errors]


def test_invalid_region_skips_timestamp_parse_instead_of_guessing():
    r = normalize_record(rec(region="APAC", timestamp_raw="03/04/2026"), NOW)
    assert [e.code for e in r.errors] == ["INVALID_REGION"]


def test_all_errors_of_a_record_are_reported():
    r = normalize_record(rec(vin="x", quality_grade="Z", timestamp_raw="03/04/2026", region=None), NOW)
    assert {e.code for e in r.errors} == {"INVALID_VIN", "INVALID_QUALITY_GRADE", "AMBIGUOUS_DATE_FORMAT"}


def test_non_object_record():
    r = normalize_record(["not", "a", "dict"], NOW)
    assert [e.code for e in r.errors] == ["NOT_AN_OBJECT"]


def test_clean_model_is_strict():
    ok = normalize_record(rec(), NOW).clean.model_dump()
    with pytest.raises(ValidationError):
        CleanLogisticsRecord(**{**ok, "timestamp_utc": "2026-04-03T10:15:00Z"})     # no milliseconds
    with pytest.raises(ValidationError):
        CleanLogisticsRecord(**{**ok, "validation_status": "QUARANTINED"})
    with pytest.raises(ValidationError):
        CleanLogisticsRecord(**{**ok, "extra": 1})


# ---- batch policy: 2 of 100 vs 10 of 100 ----

def test_2_errors_in_100_commits_98_and_quarantines_2(store):
    records = [bad(i) if i in (17, 64) else rec(i) for i in range(100)]
    res = ingest_batch(records, store, now=NOW, batch_id="b-2of100")
    assert res.status is BatchStatus.COMMITTED_WITH_QUARANTINE
    assert (res.valid, res.invalid, res.clean_written, res.quarantined_written) == (98, 2, 98, 2)
    assert res.error_rate == 0.02 and not res.needs_operator_review
    assert store.count_clean() == 98
    q = store.quarantine()
    assert [r["record_id"] for r in q] == ["R-0017", "R-0064"]
    assert all(r["error_codes"] == "AMBIGUOUS_DATE_FORMAT" and r["validation_status"] == "QUARANTINED" for r in q)
    assert q[0]["raw"]["timestamp_raw"] == "03/04/2026"          # original record kept for review
    [audit] = store.audits()
    assert audit["event"] == "BATCH_COMMITTED_WITH_QUARANTINE" and audit["valid"] == 98


def test_10_errors_in_100_rolls_back_whole_batch(store):
    records = [bad(i) if i % 10 == 0 else rec(i) for i in range(100)]
    res = ingest_batch(records, store, now=NOW, batch_id="b-10of100")
    assert res.status is BatchStatus.ROLLED_BACK
    assert res.event == "BATCH_ROLLBACK_EXCEEDED_ERROR_THRESHOLD" and res.needs_operator_review
    assert (res.clean_written, res.quarantined_written) == (0, 0)
    assert store.count_clean() == 0 and store.quarantine() == []
    [audit] = store.audits()
    assert audit["status"] == "ROLLED_BACK" and audit["needs_operator_review"] is True
    assert audit["error_rate"] == 0.1 and audit["threshold"] == 0.05
    assert len(audit["details"]["rejected"]) == 10                 # reasons kept for the operator
    assert audit["details"]["error_counts"] == {"AMBIGUOUS_DATE_FORMAT": 10}


def test_exactly_at_threshold_commits_just_above_rolls_back(store):
    at = ingest_batch([bad(i) if i < 5 else rec(i) for i in range(100)], store, now=NOW)
    assert at.status is BatchStatus.COMMITTED_WITH_QUARANTINE
    above = ingest_batch([bad(i) if i < 106 else rec(i) for i in range(100, 200)], store, now=NOW)
    assert above.status is BatchStatus.ROLLED_BACK and store.count_clean(above.batch_id) == 0


def test_clean_batch_and_custom_threshold(store):
    assert ingest_batch([rec(i) for i in range(10)], store, now=NOW).status is BatchStatus.COMMITTED_CLEAN
    res = ingest_batch([bad(i) if i < 12 else rec(i) for i in range(10, 20)], store, now=NOW, threshold=0.2)
    assert res.status is BatchStatus.COMMITTED_WITH_QUARANTINE and res.error_rate == 0.2
    res = ingest_batch([bad(i) if i < 21 else rec(i) for i in range(20, 30)], store, now=NOW, threshold=0.0)
    assert res.status is BatchStatus.ROLLED_BACK


@pytest.mark.parametrize("t", [-0.01, 0.51, 1.0, float("nan"), True, "0.05"])
def test_threshold_cannot_be_loosened_past_bounds(t):
    with pytest.raises(ValueError):
        validate_threshold(t)


def test_empty_batch_rejected(store):
    with pytest.raises(ValueError):
        ingest_batch([], store, now=NOW)
    assert store.audits() == []


def test_duplicate_ids_in_batch_and_across_batches_are_quarantined(store):
    ingest_batch([rec(i) for i in range(50)], store, now=NOW)
    again = ingest_batch([rec(i) for i in range(50)], store, now=NOW)       # replayed feed
    assert again.status is BatchStatus.ROLLED_BACK and again.error_counts == {"DUPLICATE_RECORD_ID": 50}
    assert store.count_clean() == 50
    records = [rec(i) for i in range(100, 140)] + [rec(100)]
    res = ingest_batch(records, store, now=NOW)
    assert res.invalid == 2 and store.count_clean(res.batch_id) == 39      # both copies held, neither guessed


def test_commit_is_atomic_when_a_write_fails(store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(DQStore, "insert_quarantine", staticmethod(boom))
    with pytest.raises(RuntimeError):
        ingest_batch([bad(0)] + [rec(i) for i in range(1, 100)], store, now=NOW)
    assert store.count_clean() == 0 and store.audits() == []


# ---- generator, report, CLI ----

def test_generator_is_deterministic_and_has_exact_error_count():
    a = generate_feed(100, 2, seed=7, now=NOW)
    assert a == generate_feed(100, 2, seed=7, now=NOW)
    results = [normalize_record(r, NOW) for r in a["records"]]
    assert sum(not r.valid for r in results) == 2 == len(a["expected_invalid"])
    assert a["synthetic"] is True


def test_generated_feed_covers_regional_formats_and_all_error_kinds():
    feed = generate_feed(200, 16, seed=1, now=NOW)
    regions = {(r["region"] or "").strip().upper() for r in feed["records"]}
    assert {"EU", "US", "IN", "UTC"} <= regions
    found = {e.code for r in feed["records"] for e in normalize_record(r, NOW).errors}
    assert {x["error"] for x in feed["expected_invalid"]} <= found


def test_report_escapes_data_and_shows_counts(store):
    ingest_batch([rec(0, record_id="<script>x</script>", vin="bad")] + [rec(i) for i in range(1, 100)], store, now=NOW)
    html = render_report(store, now=NOW)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "99" in html and "INVALID_VIN" in html and "Synthetic" in html


def test_cli_end_to_end(tmp_path, capsys):
    db, feed, out = str(tmp_path / "c.db"), tmp_path / "f.json", tmp_path / "r.html"
    assert cli.main(["--db", db, "generate", "--count", "100", "--error-count", "2", "--out", str(feed)]) == 0
    assert cli.main(["--db", db, "ingest", "--file", str(feed), "--json"]) == 0
    result = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert result["status"] == "COMMITTED_WITH_QUARANTINE" and result["clean_written"] == 98
    feed10 = tmp_path / "f10.json"
    assert cli.main(["--db", db, "generate", "--count", "100", "--error-count", "10", "--seed", "3",
                     "--out", str(feed10)]) == 0
    assert cli.main(["--db", db, "ingest", "--file", str(feed10)]) == 2
    assert cli.main(["--db", db, "show-quarantine"]) == 0
    text = capsys.readouterr().out
    assert "quarantined records: 2" in text and "BATCH_ROLLBACK_EXCEEDED_ERROR_THRESHOLD" in text
    assert cli.main(["--db", db, "report", "--out", str(out)]) == 0 and out.read_text().startswith("<!doctype html>")
    assert cli.main(["--db", db, "ingest", "--file", str(tmp_path / "missing.json")]) == 1
    assert cli.main(["--db", db, "ingest", "--file", str(feed), "--threshold", "0.9"]) == 1
