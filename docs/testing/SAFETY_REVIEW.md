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

---

# Independent Safety Review — Day 2 (service_crash + guardrails)

Reviewer: independent read-only subagent, 2026-10-10. Scope: `faults.py`, `guardrails.py`, `orchestrator.py`,
`safety.py`, `docker_cli.py`, `config.py`, `kill-switch.sh`, config. **No high-severity issues.**

| ID | Sev | Finding | Fix | Test |
|---|---|---|---|---|
| M1 | Med | The watchdog could fire during `inject()` before `docker stop` landed, then never fire again; a late stop could outlive the 30 s bound. | `inject()` and `rollback()` share the fault lock. Once a rollback has started, `inject()` raises `InjectAborted`; a slow stop finishes first and is then undone. | `test_M1_*` (2) |
| M2 | Med | After a late stop, the orchestrator's real `docker start` was credited to the watchdog's earlier, wrong "verified". | Credit whoever performed the restore step; record "earlier verification … superseded". | `test_M2_*` |
| M3 | Med | No upper bounds on guard config, so a guard could be configured to never trip inside 30 s. | Caps: `max_error_rate` ≤ 0.5 (global and per experiment), `error_min_samples` ≤ 10, latency multiplier ≤ 20, floor ≤ 5000 ms, window ≤ 30, snapshot failures ≤ 5. | bound tests in `test_guardrails.py` |
| M4 | Med | `startup_recovery` could `docker start` a target an operator stopped on purpose (stale lock only, or a pause marker). | The marker records the container id at precheck. Startup recovery starts a target only if the marker says `docker_stop` for that exact id; otherwise it reports "NOT started" and latches. | `test_M4_*` (3) |
| L1 | Low | With no recorded id, rollback could start a stopped container it never stopped. | The id is bound at precheck; `inject` refuses if the container changed since; rollback refuses to start an unrecorded id. | `test_L1_*` (2) |
| L2 | Low | `kill-switch.sh` inspected without `--type container`. | Added to every inspect. | live: exit 0 |
| L3 | Low | Blast monitor stopped but not joined. | `join(timeout=2)`. | — |
| L4 | Low | Final blast check skipped when the pre-inject snapshot was empty. | Refuse to inject if the baseline is empty or lacks the target. | `test_refuses_to_inject_when_target_missing_from_blast_baseline` |
| L5 | Low | A `docker stop` the daemon applies after the CLI's 15 s timeout is only caught by the recovery check. | Accepted: that path ends LATCHED_UNSAFE. Documented. | — |

Result after fixes: `pytest -q` → 140 passed; `RP_LIVE=1` → 143 passed; post-fix live runs in `reports/day2/README.md`.
