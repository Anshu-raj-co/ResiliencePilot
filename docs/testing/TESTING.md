# Testing

```bash
cd backend
.venv/Scripts/python -m pytest -q             # unit tests (no Docker needed)
RP_LIVE=1 .venv/Scripts/python -m pytest -q   # + read-only checks against the running stack
```
Setup: `python -m venv .venv && .venv/Scripts/python -m pip install -e ".[test]"` (Windows paths; use `.venv/bin` elsewhere).

## Day-1 results (2026-10-10)
- `pytest -q`: **38 passed**. `RP_LIVE=1 pytest -q`: **41 passed** (≈24 s).

## What the unit tests cover
Fakes in `tests/helpers.py` model the real blueprint: telemetry `createdDateTime` advances only while the fake
forwarder container is unpaused, so detection and recovery logic is exercised end to end without Docker.

| Required case (brief §7 Phase 2) | Test |
|---|---|
| success | `test_success_injects_detects_rolls_back_and_verifies_recovery` |
| timeout | `test_watchdog_rolls_back_when_orchestrator_loop_hangs` |
| cancellation | `test_emergency_stop_rolls_back_early_and_blocks_new_runs_until_reset`, `test_kill_switch_file_is_honoured` |
| invalid target | `test_malformed_or_invalid_requests_rejected_without_side_effects` (influxDB, shell injection), `test_target_from_other_compose_project_refused` |
| malformed request | same parametrised test: unknown type/field, wrong types, >30 s, blank approver, non-object |
| concurrent request | `test_concurrent_request_rejected_while_active` |
| observer loss | `test_observer_loss_during_fault_triggers_rollback` |
| rollback failure | `test_rollback_failure_latches_and_blocks_everything` |
| recovery failure | `test_recovery_failure_latches_even_though_container_is_running` |
| extra | precheck refusal, fault-not-verified, crash/startup recovery, latch persistence, reset rules |

Telemetry tests (`test_telemetry.py`) use a body captured from the live API and cover: nanosecond timestamps,
empty status list, frozen timestamp with HTTP 200 (hidden failure), progression-only detection, regressions.

## Live experiments run manually (evidence in `reports/day1/`)
See `docs/recon.md` §8: 10 s stall, kill switch from another process, hard-kill crash test.
