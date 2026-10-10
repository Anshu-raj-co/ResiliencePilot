# Current Status

- **Milestone:** Day 1 COMPLETE — Phases 0, 1, 2 verified live; safety review fixes applied; demo rehearsed
- **Branch:** `wip/day1-recon` (pushed). `main` = `401a23d` (PR #1 merged); wrap-up in PR #2.

## Acceptance criteria

Phase 1
- [x] Local SDV integration runs (8 containers, `scripts/stack-up.sh`)
- [x] Actual telemetry observed (`createdDateTime` via rFMS API)
- [x] Baseline contains real measurements (`reports/day1/baseline-20261010T084740Z.json`)
- [x] Stale/non-advancing telemetry detected by tested logic (`test_telemetry.py`)
- [x] Setup reproducible (submodule + scripts; CRLF and overlay-network issues handled)

Phase 2
- [x] Fault affects the verified path (forwarder paused → `createdDateTime` frozen, API still 200)
- [x] Actual impact recorded (TTD 5.26 s, max age 10.06 s, 10 healthy-but-stale observations)
- [x] Rollback occurs and is verified (`docker inspect`)
- [x] Recovery independently verified from telemetry (first fresh 1.04 s, verified 3.14 s)
- [x] Failure cases block unsafe continuation (rollback/recovery failure → latch; crash → startup latch; tested)
- [x] Kill switch tested live (0.6 s to verified rollback) and with the backend hard-killed

## Tests (last run)
- `pytest -q` → 49 passed; `RP_LIVE=1 pytest -q` → 52 passed.
- `scripts/demo-day1.sh` → exit 0, 40 s (log: `docs/demo/day1-demo-run.log`).
- Live cross-process check: second terminal `status` shows BUSY, `run` refused, live run unaffected.

## Known limitations
- Safety review: `docs/testing/SAFETY_REVIEW.md` (H1, M1–M5, L1–L4 fixed; L5 documented).
- Watchdog is in-process; a hard-killed backend leaves the fault until `kill-switch.sh` or the next backend start.
- TTD is bounded below by the 5 s threshold (chosen from baseline headroom).
- No AI, UI or API server yet (Phases 4–5). Only one experiment type.

## Blockers
- None.
