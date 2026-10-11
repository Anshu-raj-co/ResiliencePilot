# Testing

```bash
cd backend
.venv/Scripts/python -m pytest -q             # unit tests (no Docker needed)
RP_LIVE=1 .venv/Scripts/python -m pytest -q   # + read-only checks against the running stack
```
Setup: `python -m venv .venv && .venv/Scripts/python -m pip install -e ".[test]"` (Windows paths; use `.venv/bin` elsewhere).

## Day-1 results (2026-10-10)
- `pytest -q`: **38 passed**. `RP_LIVE=1 pytest -q`: **41 passed** (≈24 s).

## Day-2 results (2026-10-10, branch feat/day2-service-crash)
- `pytest -q`: **140 passed, 3 skipped**. `RP_LIVE=1 pytest -q`: **143 passed**.
- New: `test_service_crash.py` (23 collected: stop/start, cap 15 s, start failure latches, no-op stop, replaced
  container never restarted, watchdog starts the container, emergency stop, startup recovery only starts a target
  stopped by our own docker_stop marker for the same container id, injection latency + exit code, and the Day-2
  safety-review regressions M1/M2/M4/L1), `test_guardrails.py` (37 collected: each guard's logic, lower AND upper
  config bounds, each guard tripping end to end for both mechanisms with verified rollback, blast-radius unobservable,
  refusal to inject when unobservable or the target is missing from the baseline, breach after the fault window
  still latches).

## Final results (2026-10-11, `main` after PR #27, macOS Apple Silicon)
- `pytest -q`: **315 passed, 11 skipped**. `RP_LIVE=1 pytest -q`: **318 passed, 8 skipped**.
- New since Day 2: picker/API (`test_api.py`), AI call logging and hypothesis labels (`test_ai.py`), config profiles
  tighten-only and CLI-only (`test_profiles.py`, `test_api.py`), platform and config profile in run records
  (`test_safety.py`). PR #29 (open) adds 2 more profile tests.

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
