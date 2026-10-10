# Roadmap

Day 1 scope (override #2): Phases 0–2 only. Phases 3–6 are listed here and **not started**.

| Phase | Goal | Status |
|---|---|---|
| 0 | Recon & spec: real blueprint, data path, fault feasibility, contracts | Day 1 |
| 1 | Real SDV pipeline running, telemetry baseline, stale-detection logic + tests, reproducible setup | Day 1 |
| 2 | Safety controller, state machine, watchdog, kill switch, telemetry-stall experiment, rollback + verified recovery, negative-path tests | Day 1 |
| 3 | Additional experiments: forwarder/consumer crash-restart (`docker stop`/`start`), consumer pause, zenoh-router pause; repeated runs, TTD/TTR stats, variability | Not started |
| 4 | Advisory AI planner + findings engine (schema-validated, deterministic fallback, prompt-injection tests; never executes faults) | Not started |
| 5 | Dashboard: dependency graph, baseline, proposals, approval, live state + emergency stop, predicted-vs-observed, history, report export | Not started |
| 6 | Clean-setup run, all experiments E2E, full test pass, docs, real screenshots, final demo script | Not started |

## Phase 3 candidates (from recon, all on the verified path)
- `pause fms-consumer` — tests whether Zenoh buffers or drops during consumer outage (expect drop: gap in data).
- `stop/start fms-forwarder` — process crash; measures reconnection to databroker + zenoh.
- `pause fms-zenoh-router` — transport outage; router has `restart: unless-stopped`.
- `stop influxdb` — expected to turn fms-server into HTTP 500 (detected by plain health checks — useful contrast).
- Network degradation: only if a mechanism truly sits on the path (e.g. `tc netem` inside the forwarder netns via a
  helper container). No proxy exists today; do not claim proxy-based faults.

## Backlog: safety-controller follow-ups (from the PR #10 safety review; not fixed)
Both are pre-existing and fail closed today; they are listed so they are not forgotten.
- **Latch write memory.** If `latch()` cannot write `safety_state.json` even after the bounded retry, it raises and
  the latch exists nowhere. Proposal: keep an in-memory latched flag that `_read_state()` honours, then re-raise.
  Needs negative-path tests and a safety review.
- **Reset ordering.** `reset()` writes `latched=False` first and then deletes the marker and `run.lock`; if a delete
  fails, the state says unlatched while a leftover file still blocks runs. Proposal: delete the files first and
  write the unlatched state last. Needs tests and a safety review.

