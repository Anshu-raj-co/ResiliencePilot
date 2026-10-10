# Day 2 Plan: three experiment types

Status: **plan only, nothing below is implemented** except `telemetry_stall`. Every "expected effect" is a
**hypothesis** to be tested, not a result. Values marked *proposed* are starting points for review.

All three experiments use the existing safety model unchanged: strict schema and allowlist, the compose-project label
check, one experiment at a time across processes, total fault time ≤ 30 s enforced by the watchdog, rollback in
`finally`, independent recovery verification, and LATCHED_UNSAFE if rollback or recovery can't be verified.

| | telemetry_stall | service_crash | dependency_failure |
|---|---|---|---|
| Status | **Done (Day 1)** | Planned | Planned |
| Mechanism | `docker pause` / `unpause` | `docker stop` / `start` | `docker stop` / `start` |
| Allowlisted target | `fms-forwarder` | `fms-forwarder` | `influxDB` |
| Duration cap | ≤ 30 s total incl. grace (used: 10–12 s) | *proposed* ≤ 15 s fault window | *proposed* ≤ 15 s fault window |

---

## 1. telemetry_stall (done)

- **Hypothesis (tested):** freezing the forwarder stops `createdDateTime` advancing while fms-server keeps answering
  HTTP 200.
- **What was measured:** confirmed in 6 full-length runs. HTTP 200 on 100 % of fault-window polls; stale detected
  4.77–5.27 s after injection; recovery verified 2.64–3.73 s after rollback (`docs/demo/DAY1_REPORT.md`).
- **Rollback:** `docker unpause`, verified by `docker inspect` (running, not paused).
- **Recovery criteria:** 3 successive advances of `createdDateTime` past the last frozen value and past the server
  time of the last fault-window poll, each with HTTP 200 and age ≤ 5 s, within 30 s.

## 2. service_crash: stop/start `fms-forwarder`

- **Hypothesis:** stopping the forwarder process produces the same visible symptom as the pause (HTTP 200, frozen
  `createdDateTime`). After `docker start`, the forwarder needs **longer** to deliver fresh data than after an
  unpause, because it must reconnect to the databroker and open a new Zenoh session. *Untested.*
- **Allowlisted target:** `fms-forwarder` only (same exact name plus compose-project label).
- **Duration cap:** *proposed* fault window ≤ 15 s. `docker stop -t 2` keeps SIGTERM→SIGKILL within the window.
  The watchdog cap stays 30 s in total.
- **Rollback:** `docker start fms-forwarder`, verified by `docker inspect` showing `State.Running=true` and an
  unchanged container ID (same container, not a recreated one).
- **Recovery criteria:** same as telemetry_stall. *Proposed:* keep `recovery_timeout_s` at 30 s. If cold-start
  reconnects take longer, raise it in config only after measuring, never above the documented ceiling.
- **Risks:**
  - The forwarder has no restart policy in Compose, so if `docker start` fails, it stays down. The system must latch
    (the existing rule) and the kill switch must learn to `start`, not only `unpause`. **Kill-switch change needed.**
  - The Zenoh client may fail to reconnect, or reconnect slowly. Recovery verification would catch this, but the run
    would latch.
  - Telemetry for the stopped window is never produced; there's no backfill.
  - A new fault adapter (`StopStartFault`) is code that changes the system. It needs the same negative-path tests as
    `PauseFault` and an independent safety review.

## 3. dependency_failure: stop/start `influxDB`

- **Hypothesis:** with InfluxDB down, fms-server answers **HTTP 500**, not 200. Inferred from source:
  `components/fms-server/src/lib.rs:92,117,146` map query errors to `INTERNAL_SERVER_ERROR`. In that case this
  failure is **visible** to a plain health check, unlike the stall. A second hypothesis: fms-consumer logs
  `failed to write data to influx` (`influx-client/src/writer.rs:348`) and drops those statuses, so after recovery
  the outage window has a **gap** in stored data. *Both untested.*
- **Allowlisted target:** `influxDB` only.
- **Duration cap:** *proposed* fault window ≤ 15 s. InfluxDB start-up time to `healthy` must be measured first;
  `docker inspect` health status is available (`influx ping` health check).
- **Rollback:** `docker start influxDB`, verified by `docker inspect` showing `Running=true` **and**
  `State.Health.Status=healthy`.
- **Recovery criteria:** HTTP 200 returns **and** the existing telemetry criteria pass (3 fresh advances of
  `createdDateTime`). *Proposed addition:* record the size of the data gap, but don't require it to be zero.
- **Risks / required design decisions before implementation:**
  - **Conflict with the current safety rule.** The orchestrator stops any run whose observer sees a non-200
    response (`stop_reason = safety_threshold`, `orchestrator.py:186`). For this experiment, HTTP 500 is the
    *expected* symptom. A per-experiment "expected symptoms" rule is needed. The safety threshold must not be
    simply disabled. **Decision for the lead.**
  - **Blast radius is wider.** InfluxDB is shared by fms-server, fms-consumer, Grafana and the excluded
    fleet-analysis-backend.
  - Data is on named volumes (`influxdb-data`, `influxdb-auth`). `docker stop`/`start` keeps them, but
    `docker compose down -v` would not. The adapter must never remove containers or volumes.
  - InfluxDB may take longer than 30 s to become healthy on a slow machine. Measure before choosing caps.

---

## Order of work (proposed)
1. Settle the open question about the 1.0 s vs 500 ms cadence (`docs/recon.md` §8), so baselines are understood.
2. Implement `service_crash`: adapter, kill-switch `start` support, tests, safety review, 3+ live runs with
   variability reported.
3. Decide the expected-symptom rule, then implement `dependency_failure` the same way.
4. Only then add the AI planner, which ranks these allowlisted experiments and never executes them.
