# Decisions Log

Format: ID — decision — why — consequences. Newest at bottom.

**D-001 — Target system: Eclipse SDV Fleet Management Blueprint @ upstream `3d514ac72a`, Zenoh transport.**
Why: already present in the repo as a gitlink to that commit; Apache-2.0; prebuilt images are published; Zenoh
variant runs fully locally (Hono variant needs the public Hono sandbox, i.e. an external system — excluded by safety rules).

**D-002 — Override `overlay` networks to `bridge`** (`config/compose/resiliencepilot.override.yaml`).
Why: `overlay` requires Docker Swarm (`docker info`: Swarm inactive). Single-host bridge networks keep the same
topology (two networks, forwarder + router dual-homed). Upstream files are not modified.

**D-003 — Exclude `fleet-analysis-backend` on Day 1.**
Why: no published image (needs local Maven/JDK build; disk has ~18 GB free) and it only reads InfluxDB — it is not on
the producer→consumer telemetry path. Can be added later for Phase 3 dependency experiments.

**D-004 — First fault = `docker pause fms-forwarder`, rollback = `docker unpause`.**
Why: forwarder is the single publisher of VehicleStatus and stamps `createdDateTime`; pausing it stalls the
freshness signal while fms-server keeps answering HTTP 200 — the exact "healthy but stale" story. Freeze/thaw is
reversible and loses no state. There is **no proxy** in the stack, so no network-proxy fault is claimed (override #9).

**D-005 — Normalize CRLF→LF in container-mounted blueprint files inside `scripts/stack-up.sh`.**
Why: Windows `core.autocrlf=true` checks out the blueprint with CRLF; influxdb's init script then fails with
`run-parts: failed to exec … No such file or directory`. Stripping CR is content-neutral for git.

**D-006 — Add `.gitmodules` for the existing gitlink.**
Why: the repo tracked `Fleet Management/fleet-management` as a gitlink (mode 160000) without `.gitmodules`, so fresh
clones got an empty directory. Now `git submodule update --init` restores the pinned upstream revision.

**D-007 — Backend in Python 3.13 (FastAPI + pydantic + httpx), Docker via the `docker` CLI.**
Why: available locally, typed, fast to test; CLI wrapper is one small module that tests replace with a fake.
No Docker socket is ever exposed to an LLM.

**D-008 — Two stale signals from `createdDateTime`: age and progression.**
- age = `requestServerDateTime − createdDateTime`. Both are stamped on the Docker VM clock (fms-server and
  fms-forwarder), so no host↔VM clock skew enters the measurement.
- progression = local time since `createdDateTime` last advanced (clock-independent; catches a stall even if
  the server clock were wrong).
STALE if either exceeds `stale_threshold_s` (5 s). There is no sequence id. `receivedDateTime` is `Utc::now()` at
request time and is recorded only to demonstrate the misleading-health discrepancy.
Threshold justification: Day-1 baseline max age 1.30 s, advance interval 1.002 s → 5 s is ~3.8× headroom (no false
positives in 60 baseline samples). Cost: time-to-detection ≈ 5 s minus the age at injection.

**D-009 — Recovery = rollback verified by `docker inspect` AND 3 successive advances of `createdDateTime` beyond the
last pre-rollback value, each with HTTP 200 and age ≤ 5 s, within 30 s.** Otherwise LATCHED_UNSAFE.

**D-010 — Emergency stop is persistent.** Any stop (CLI, Ctrl+C, kill-switch file) blocks new runs until a checked
`reset` (all allowlisted targets running + unpaused). Conservative by design.

**D-011 — Crash safety is layered:** fault marker written *before* `docker pause`; external `scripts/kill-switch.sh`
unpauses without the backend; next backend start runs `startup_recovery` (unpause if needed + latch + annotate the
run record). Known limit: the in-process watchdog dies with a hard-killed process (verified live), so between crash
and kill-switch/restart the fault persists.

**D-012 — service_crash = `docker stop -t 2` / `docker start` (`StopStartFault`).** Rollback is verified only for the
**same container id** recorded before the stop; a removed or replaced container is reported as not restored (latch),
never recreated. `stop_timeout_s` is bounded to 0..5 (catalogue) and 0..10 (Docker wrapper). Catalogue cap 15 s.

**D-013 — Guardrails detect, the orchestrator rolls back.** Error-rate and latency guards run in the fault loop; the
blast-radius guard is a separate read-only monitor thread (a whole-project `docker inspect` costs ~66 ms by container id,
but each Docker CLI call is ~190 ms on this machine, so checking inside the 0.5 s poll loop would distort cadence).
Thresholds: error rate 0.0 by default (identical to Day 1's "any non-200 aborts"), with an optional per-experiment
tolerance judged only after 5 responses (one early error is not a 100 % rate); latency p95 > max(5 x p95 of the newest
stored baseline, 250 ms) over the last 10 polls (min 5); blast radius compares id, status and StartedAt (a restart
keeps status `running` but changes StartedAt; verified live with Grafana).

**D-014 — Detection time is recorded twice.** `time_to_detection_s` is measured from the fault command (as on Day 1);
`time_to_detection_from_verified_injection_s` subtracts `injection_latency_s`. For service_crash they differ by the
~2.4 s stop grace because fms-forwarder ignores SIGTERM (exit code 137, verified).

**D-015: The expected-status rule (dependency_failure).** An experiment may declare `expected_statuses` (HTTP
400–599). During the fault window only, those codes count as the symptom, never as errors. Any other non-200 still
counts for the error-rate guard. If a symptom is declared, the first poll with no response aborts (observer loss
limit 1, instead of 4). Precheck and recovery are unchanged. Approved by the lead.

**D-016: Stored-data gap as evidence.** After recovery is verified, one read-only `starttime/stoptime` query
records the largest gap between stored `createdDateTime` values around the fault. It is evidence only: a failure here
is recorded and never changes the run's state.

**D-017: Per-experiment response envelope (approved option b).** An experiment may declare
`fault_observer_timeout_s` (at most 5 s) and `fault_latency_p95_max_ms` (at most 5000 ms, and below the timeout). Both
apply to fault-window polls only. Duration plus timeout must be at most 30 s, and a request's duration must exceed the
timeout. The fault loop never starts a poll that could outlive the window, so the watchdog stays a backstop. Precheck
and recovery keep the global 2 s timeout. Used by `dependency_failure` (5 s / 4.5 s).

**D-018: `reset` requires fresh, advancing telemetry.** Besides healthy containers, `reset` needs 3 consecutive FRESH
polls (HTTP 200, age at most 5 s) with at least one advance of `createdDateTime`, within 15 s. Containers are
re-checked after polling, the check is recorded in the reset history, and without an observer reset refuses. This
prevents the 2026-10-10 operator error (reset while the API answered 500).


**D-019 — Resilience Score = weighted mean of measured components × silent-failure multiplier (`docs/SCORING.md`).**
Detection 0.25, recovery 0.30, data integrity 0.20, blast radius 0.25; unmeasured components drop out (never imputed);
`× (1 − 0.5 · silent ratio)` over fault + recovery polls. Only `duration_elapsed` runs enter the per-type summary;
runs cut short by the system's response are scored but `partial`; operator/tool stops and unverified rollbacks are
not scorable. Read-only: computed from stored run records, never fed back into the safety path.

**D-020 — AI layer is advisory, local and evidence-gated (`docs/AI_LAYER.md`).** Local Ollama only (loopback URL
enforced); `ai.py` may not import anything that reaches Docker, the shell or safety state (test-enforced). The
planner may only reorder rule-based candidates (exact permutation, else fallback). Analyst items must cite their own
finding's runs and pass a deterministic grounding check against the verified facts; failures fall back per item to
the rule-based knowledge base with the reason and the model's text recorded. Rule-based answers are always available.
