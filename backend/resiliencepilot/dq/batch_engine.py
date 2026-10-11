"""Batch gate: "2 errors out of 100: roll back or process?" (docs/DATA_QUALITY.md).

error_rate <= threshold -> one atomic transaction commits the valid records and quarantines the invalid ones with
                           their reasons (COMMITTED_WITH_QUARANTINE, or COMMITTED_CLEAN when nothing failed).
error_rate >  threshold -> the whole batch is rolled back: 0 clean rows, 0 quarantine rows, one audit event
                           BATCH_ROLLBACK_EXCEEDED_ERROR_THRESHOLD flagged for operator review, carrying every
                           rejected record's reasons so the operator can see what went wrong.
"""
from __future__ import annotations

import secrets
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from .normalizer import NormalizationResult, normalize_record, to_canonical
from .schema import DQError
from .store import DQStore

DEFAULT_QUARANTINE_THRESHOLD = 0.05
MAX_QUARANTINE_THRESHOLD = 0.5     # config may lower it, never raise it past this


class BatchStatus(StrEnum):
    COMMITTED_CLEAN = "COMMITTED_CLEAN"
    COMMITTED_WITH_QUARANTINE = "COMMITTED_WITH_QUARANTINE"
    ROLLED_BACK = "ROLLED_BACK"


EVENT_FOR = {
    BatchStatus.COMMITTED_CLEAN: "BATCH_COMMITTED",
    BatchStatus.COMMITTED_WITH_QUARANTINE: "BATCH_COMMITTED_WITH_QUARANTINE",
    BatchStatus.ROLLED_BACK: "BATCH_ROLLBACK_EXCEEDED_ERROR_THRESHOLD",
}


@dataclass(frozen=True)
class BatchResult:
    batch_id: str
    status: BatchStatus
    event: str
    total: int
    valid: int
    invalid: int
    error_rate: float
    threshold: float
    clean_written: int
    quarantined_written: int
    needs_operator_review: bool
    error_counts: dict[str, int]

    def as_dict(self) -> dict:
        return {**self.__dict__, "status": self.status.value}


def validate_threshold(threshold: float) -> float:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) \
            or not 0.0 <= threshold <= MAX_QUARANTINE_THRESHOLD:
        raise ValueError(f"quarantine threshold must be a number in 0.0..{MAX_QUARANTINE_THRESHOLD}, got {threshold!r}")
    return float(threshold)


def new_batch_id(now: datetime) -> str:
    return f"{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


def _mark_duplicates(results: list[NormalizationResult], committed: dict[str, str]) -> list[NormalizationResult]:
    """A record id seen twice in the batch, or already committed, is quarantined. Which copy is right is not guessed:
    every occurrence of an in-batch duplicate is quarantined."""
    counts = Counter(r.record_id for r in results if r.record_id is not None)
    out = []
    for r in results:
        extra: list[DQError] = []
        if r.record_id is not None and counts[r.record_id] > 1:
            extra.append(DQError(code="DUPLICATE_RECORD_ID", field="record_id",
                                 detail=f"{r.record_id!r} appears {counts[r.record_id]} times in this batch"))
        if r.record_id is not None and r.record_id in committed:
            extra.append(DQError(code="DUPLICATE_RECORD_ID", field="record_id",
                                 detail=f"{r.record_id!r} already committed by batch {committed[r.record_id]}"))
        out.append(NormalizationResult(r.record_id, r.raw, None, r.errors + tuple(extra)) if extra else r)
    return out


def ingest_batch(records: list[Any], store: DQStore, *, threshold: float = DEFAULT_QUARANTINE_THRESHOLD,
                 now: datetime | None = None, batch_id: str | None = None, source: str | None = None) -> BatchResult:
    threshold = validate_threshold(threshold)
    if not isinstance(records, list) or not records:
        raise ValueError("a batch must be a non-empty list of records")
    now = now or datetime.now(timezone.utc)
    batch_id = batch_id or new_batch_id(now)
    at = to_canonical(now)

    with store.transaction() as conn:
        results = [normalize_record(r, now) for r in records]
        ids = sorted({r.record_id for r in results if r.record_id is not None})
        results = _mark_duplicates(results, store.existing_record_ids(conn, ids))

        valid = [r for r in results if r.valid]
        invalid = [r for r in results if not r.valid]
        total = len(results)
        error_rate = len(invalid) / total
        error_counts = dict(sorted(Counter(e.code for r in invalid for e in r.errors).items()))

        if error_rate > threshold:
            status = BatchStatus.ROLLED_BACK
            clean_written = quarantined_written = 0
            details = {"error_counts": error_counts,
                       "reason": f"error rate {error_rate:.2%} > threshold {threshold:.2%}; whole batch rolled back",
                       "rejected": [{"record_id": r.record_id, "errors": [e.model_dump() for e in r.errors]}
                                    for r in invalid]}
        else:
            status = BatchStatus.COMMITTED_WITH_QUARANTINE if invalid else BatchStatus.COMMITTED_CLEAN
            for r in valid:
                store.insert_clean(conn, batch_id, r.clean, at)
            for r in invalid:
                store.insert_quarantine(conn, batch_id, r.record_id, r.raw, r.errors, at)
            clean_written, quarantined_written = len(valid), len(invalid)
            details = {"error_counts": error_counts}

        needs_review = status is BatchStatus.ROLLED_BACK
        store.insert_audit(conn, batch_id=batch_id, event=EVENT_FOR[status], status=status.value, source=source,
                           total=total, valid=len(valid), invalid=len(invalid), error_rate=error_rate,
                           threshold=threshold, needs_operator_review=needs_review, details=details, at=at)

    return BatchResult(batch_id=batch_id, status=status, event=EVENT_FOR[status], total=total, valid=len(valid),
                       invalid=len(invalid), error_rate=error_rate, threshold=threshold, clean_written=clean_written,
                       quarantined_written=quarantined_written, needs_operator_review=needs_review,
                       error_counts=error_counts)
