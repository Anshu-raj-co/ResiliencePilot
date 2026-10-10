# Independent Safety Review — Day 1

Reviewer: an independent read-only Claude subagent (no file edits, no Docker access), 2026-10-10.
Scope: `safety.py`, `faults.py`, `orchestrator.py`, `docker_cli.py`, `telemetry.py`, `__main__.py`, `kill-switch.sh`,
config, contracts, tests. Every finding was checked against the code before it was fixed.

| ID | Sev | Finding | Fix | Test |
|---|---|---|---|---|
| H1 | High | The single-run lock was in memory only, so a second CLI process could start a run or `reset` mid-experiment and overwrite or clear the shared marker. | `run.lock` is created with `O_CREAT\|O_EXCL` and holds the owner PID. PID liveness is checked through the Win32 `OpenProcess` API (never `os.kill`). `reset` refuses while a live owner exists. A stale lock left by a dead process latches. | `test_H1_*` (3); verified live with a second terminal |
| M1 | Med | `startup_recovery` ran before every CLI command, so `status` from another terminal treated a live run as a crash. | Recovery is skipped while another live process holds the run lock. | `test_M1_*`; verified live |
| M2 | Med | The rollback result was cached. A `docker pause` that landed after the watchdog fired could stay in place while the record said "verified". | `rollback()` always re-inspects the container. The original actor is kept in the record ("re-verified by …"). | `test_M2_late_landing_pause_is_still_rolled_back` |
| M3 | Med | The watchdog deadline was duration + grace (35 s), and grace was unbounded. | Deadline is `min(duration + grace, 30 s)`. `watchdog_grace_s` is capped at 10 s. | `test_M3_*` |
| M4 | Med | The marker was written non-atomically. A corrupt marker or state file crashed `stop`, `reset` and `status`. | Marker writes are atomic. Unreadable files count as latched or unknown, and recovery then checks every allowlisted target. The CLI handles `stop` before reading any state. | `test_M4_*` |
| M5 | Med | The Ctrl+C handler took a non-reentrant lock, so it could deadlock during `latch()`. | The handler calls `signal_stop()`, which takes no lock and does no I/O. The lock is now an `RLock`. | `test_M5_*` |
| L1 | Low | Runs that stopped early (emergency stop, observer lost, watchdog) were recorded as COMPLETED. | COMPLETED is used only for a verified fault with `duration_elapsed` and no latch. Everything else is ABORTED or LATCHED_UNSAFE. | `test_L1_*`, emergency-stop test |
| L2 | Low | A message that was in flight before the pause could count as a recovery advance. | A recovery sample must also be newer than the fms-server time of the last poll in the fault window. | covered by the success and recovery tests |
| L3 | Low | Under `set -e`, `kill-switch.sh` could exit early. A failed unpause was masked, and it reported success. | No `-e`. Unpause is retried 3×, the final state is verified, and the script exits non-zero if anything is still paused. It reads its config from `config/*.json`. | exercised live in `scripts/demo-day1.sh` |
| L4 | Low | Startup recovery skipped the compose-project check. | Startup recovery now checks the project label before it unpauses. | `test_startup_recovery_*` |
| L5 | Low | The watchdog runs in-process, so a hard kill leaves the fault in place. | Documented as a known limitation (DECISIONS D-011). Mitigated by the external kill switch and startup recovery; the crash test was verified live. | live crash test (`reports/day1/…baf896.json`) |

Result after fixes: `pytest -q` → 49 passed (unit); `RP_LIVE=1` → 52 passed.
