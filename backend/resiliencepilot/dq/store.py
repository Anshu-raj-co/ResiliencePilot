"""SQLite store for the data-quality gate: clean records, quarantine, batch audit (docs/DATA_QUALITY.md).

Separate from the experiment evidence store; default path `backend/data/dq/data_quality.db` (gitignored).
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from ..config import REPO_ROOT
from .schema import CleanLogisticsRecord, DQError

DEFAULT_DB = REPO_ROOT / "backend" / "data" / "dq" / "data_quality.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS dq_clean_records (
    record_id           TEXT PRIMARY KEY,
    batch_id            TEXT NOT NULL,
    vin                 TEXT NOT NULL,
    timestamp_raw       TEXT NOT NULL,
    timestamp_utc       TEXT NOT NULL,
    region              TEXT,
    cargo_temperature_c REAL,
    quality_grade       TEXT,
    shelf_life_hours    REAL,
    truck_status        TEXT,
    validation_status   TEXT NOT NULL CHECK (validation_status = 'VALID'),
    ingested_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dq_quarantine (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id          TEXT NOT NULL,
    record_id         TEXT,
    validation_status TEXT NOT NULL CHECK (validation_status = 'QUARANTINED'),
    error_codes       TEXT NOT NULL,
    errors_json       TEXT NOT NULL,
    raw_json          TEXT NOT NULL,
    quarantined_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dq_batch_audit (
    batch_id              TEXT PRIMARY KEY,
    event                 TEXT NOT NULL,
    status                TEXT NOT NULL,
    source                TEXT,
    total                 INTEGER NOT NULL,
    valid                 INTEGER NOT NULL,
    invalid               INTEGER NOT NULL,
    error_rate            REAL NOT NULL,
    threshold             REAL NOT NULL,
    needs_operator_review INTEGER NOT NULL,
    details_json          TEXT NOT NULL,
    created_at            TEXT NOT NULL
);
"""


class DQStore:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=10.0)   # explicit BEGIN/COMMIT below
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One atomic write transaction. BEGIN IMMEDIATE takes the write lock up front so the duplicate check and the
        inserts see the same state; any exception rolls everything back."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # ---- writes (inside a transaction) ----

    @staticmethod
    def existing_record_ids(conn: sqlite3.Connection, ids: list[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT record_id, batch_id FROM dq_clean_records WHERE record_id IN ({','.join('?' * len(chunk))})"
            found.update({r["record_id"]: r["batch_id"] for r in conn.execute(q, chunk)})
        return found

    @staticmethod
    def insert_clean(conn: sqlite3.Connection, batch_id: str, rec: CleanLogisticsRecord, at: str) -> None:
        conn.execute(
            "INSERT INTO dq_clean_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec.record_id, batch_id, rec.vin, rec.timestamp_raw, rec.timestamp_utc,
             rec.region.value if rec.region else None, rec.cargo_temperature_c,
             rec.quality_grade.value if rec.quality_grade else None, rec.shelf_life_hours,
             rec.truck_status.value if rec.truck_status else None, rec.validation_status.value, at))

    @staticmethod
    def insert_quarantine(conn: sqlite3.Connection, batch_id: str, record_id: str | None, raw: Any,
                          errors: tuple[DQError, ...], at: str) -> None:
        conn.execute(
            "INSERT INTO dq_quarantine (batch_id, record_id, validation_status, error_codes, errors_json, raw_json, "
            "quarantined_at) VALUES (?,?,?,?,?,?,?)",
            (batch_id, record_id, "QUARANTINED", ",".join(sorted({e.code for e in errors})),
             json.dumps([e.model_dump() for e in errors]), json.dumps(raw, default=str, sort_keys=True), at))

    @staticmethod
    def insert_audit(conn: sqlite3.Connection, *, batch_id: str, event: str, status: str, source: str | None,
                     total: int, valid: int, invalid: int, error_rate: float, threshold: float,
                     needs_operator_review: bool, details: dict, at: str) -> None:
        conn.execute(
            "INSERT INTO dq_batch_audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (batch_id, event, status, source, total, valid, invalid, error_rate, threshold,
             int(needs_operator_review), json.dumps(details, default=str, sort_keys=True), at))

    # ---- reads ----

    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute(sql, args)]

    def count_clean(self, batch_id: str | None = None) -> int:
        if batch_id is None:
            return self._rows("SELECT COUNT(*) AS n FROM dq_clean_records")[0]["n"]
        return self._rows("SELECT COUNT(*) AS n FROM dq_clean_records WHERE batch_id = ?", (batch_id,))[0]["n"]

    def clean_records(self, batch_id: str | None = None) -> list[dict]:
        if batch_id is None:
            return self._rows("SELECT * FROM dq_clean_records ORDER BY record_id")
        return self._rows("SELECT * FROM dq_clean_records WHERE batch_id = ? ORDER BY record_id", (batch_id,))

    def quarantine(self, batch_id: str | None = None, limit: int | None = None) -> list[dict]:
        sql = "SELECT * FROM dq_quarantine" + (" WHERE batch_id = ?" if batch_id else "") + " ORDER BY id"
        args: tuple = (batch_id,) if batch_id else ()
        if limit is not None:
            sql, args = sql + " LIMIT ?", args + (limit,)
        rows = self._rows(sql, args)
        for r in rows:
            r["errors"] = json.loads(r.pop("errors_json"))
            r["raw"] = json.loads(r.pop("raw_json"))
        return rows

    def audits(self) -> list[dict]:
        rows = self._rows("SELECT * FROM dq_batch_audit ORDER BY created_at, batch_id")
        for r in rows:
            r["details"] = json.loads(r.pop("details_json"))
            r["needs_operator_review"] = bool(r["needs_operator_review"])
        return rows
