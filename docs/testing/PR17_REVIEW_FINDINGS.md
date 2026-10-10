# Safety review of PR #17 (envelope D-017 + telemetry-checked reset D-018): open findings

The review was run by an independent read-only subagent on 2026-10-11, against commit `f1cb274`. PR #17 had already been
merged (`8d0edc5` → `main`) before the review finished, so **none of these are fixed yet**. Items M1, M2 and M4 were
re-checked on `origin/main` at `be727d4` and are still present. **No high-severity issues.**

| ID | Sev | Finding (file as of `f1cb274`) | Scenario | Proposed fix |
|---|---|---|---|---|
| M1 | Med | `api.py`: `async def reset` calls `ctl.reset()` synchronously; the telemetry check sleeps for up to ~15 s. | While a dashboard reset is polling, uvicorn serves nothing, including `/api/kill` and `/api/status`. No fault can be active (latched or stop engaged), but Kill is unresponsive for ~17 s. | Make it `def reset` (threadpool) or use `run_in_threadpool`; add a test that `/api/kill` answers during a reset. |
| M2 | Med | `safety.py` reset phase 2 re-checks only "no active run" and the containers. | `kill-switch.sh` or a new latch arrives during the 15 s poll; reset then deletes `EMERGENCY_STOP` and clears a latch the operator never saw. | Snapshot the latch reason/since, stop-file presence and marker in phase 1; refuse in phase 2 if any changed. |
| M3 | Med | `safety.py` reset unlinks `run.lock` under the in-process lock only (pre-existing). | Another process takes `run.lock` with O_EXCL between the phase-2 check and the unlink. | Reset takes `run.lock` itself (O_EXCL) for the duration, or unlinks only when the holder is dead. |
| M4 | Med | `sdv_adapter.py`: httpx `timeout=5` applies to each phase (connect, read, write, pool) separately. | A slow connect plus a slow read can exceed 5 s, so "no poll outlives the window" is not guaranteed; the watchdog rolls back, but the loop then records `observer_lost` instead of `watchdog_timeout`. | Use a total deadline (e.g. `httpx.Timeout(t, connect=t/2)` plus an overall cap); check `watchdog.fired` before labelling `observer_lost`. |
| L1 | Low | An envelope without `expected_statuses` keeps `loss_limit=4` (~20 s of silence before abort). | A future spec sets an envelope but no symptom. | Require `expected_statuses` whenever an envelope is set, or force `loss_limit=1`. |
| L2 | Low | The skip-poll logic is sound; the last ≤5 s of the window go unobserved, and an emergency stop can wait behind an in-flight poll. | — | Document. |
| L3 | Low | `check_fresh_telemetry`: with `data_age_s is None` (no server time), one advance plus 3 non-stale samples passes. | Weaker than recovery's 3 advances. | Treat age `None` as not fresh. |
| L4 | Low | A non-httpx exception from `observe()` escapes reset as a raw exception (HTTP 500 / CLI traceback). | — | Wrap it into `SafetyError`. |
| L5 | Low | `config.py`: the `MAX_ERROR_RATE` comment moved onto `MAX_FAULT_LATENCY_P95_MS`. | — | Fix the comments. |

Missing negative tests suggested by the reviewer: an emergency stop or latch change during reset polling (M2); another
process taking the run lock during polling (M3); API reset must not block `/api/kill` (M1); an observer that raises in
`check_fresh_telemetry` (L4); age `None` with a single advance (L3); watchdog firing while a poll blocks, where
stop_reason must be `watchdog_timeout` (M4); an envelope spec without `expected_statuses` (L1).

## Discovery fixes on this branch (not on `main` as of `be727d4`)
`wip/claude-discover-fixes` contains a `discover.py` with two false-positive fixes found during live testing:
- **Command-line file paths are not dependencies.** `/app/run` matched a container named `app`.
- **Bind-mounted files only count host-shaped mentions** (`scheme://host`, `tcp/host`, `host:port`, `@host`). The
  Grafana dashboard JSON's datasource **type** `influxdb` was being read as a host reference. With the fix, the live
  Grafana → InfluxDB edge is honestly `low` (depends_on only), because its datasource URL lives in a provisioned volume.

Both have tests in `backend/tests/test_discover.py` on that branch. They need porting onto `main`'s `discover.py`,
which was written independently.
