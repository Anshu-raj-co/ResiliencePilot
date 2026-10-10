# Day 2 evidence: service_crash + guardrails (live runs, 2026-10-10)

Every file here is a run record written by the orchestrator on the live local stack (Eclipse SDV Fleet Management
blueprint @ `3d514ac72a`). Nothing is edited or simulated. UTC times.

## Runs

| Run | Experiment | What it shows | Result |
|---|---|---|---|
| `20261010T181417Z-c1ab0d` | telemetry_stall 10 s | Day-1 behaviour unchanged with guardrails armed (regression) | COMPLETED |
| `20261010T181441Z-920732` | service_crash 12 s | live run 1 | COMPLETED |
| `20261010T181503Z-31eafe` | service_crash 12 s | live run 2 | COMPLETED |
| `20261010T181525Z-8fa234` | service_crash 12 s | live run 3 | COMPLETED |
| `20261010T182331Z-b89ae0` | service_crash 12 s | live run 4: first run recording exit code + injection latency | COMPLETED |
| `20261010T182400Z-6f6559` | service_crash 15 s | backend hard-killed mid-fault → `kill-switch.sh` printed `started fms-forwarder` → startup recovery latched | LATCHED_UNSAFE (by design) |
| `20261010T182429Z-b10eff` | service_crash 15 s | backend hard-killed mid-fault, **no** kill switch → next start: startup recovery `fms-forwarder: started`, latched | LATCHED_UNSAFE (by design) |
| `20261010T182500Z-4aa9f7` | service_crash 15 s | blast-radius guard: `docker restart grafana` (outside the radius) during the fault → guard tripped, forwarder rolled back, recovery verified, run latched | LATCHED_UNSAFE (by design) |

## service_crash vs telemetry_stall (measured)

| Metric | service_crash (4 full runs) | telemetry_stall |
|---|---|---|
| HTTP 200 during fault | 100 % (all 4) | 100 % (regression run; and all 6 Day-1 runs) |
| `docker stop` duration (stop command → container verified stopped) | 2.46 / 2.41 / 2.45 / 2.40 s (runs 1–4) ¹ | 0.11 s (pause) |
| Exit code of the stopped forwarder | **137** (SIGKILL after the 2 s grace) ² | n/a |
| Time to detection from the fault command | 7.22–7.32 s | 4.92 s (Day 1: 4.77–5.27 s) |
| Time to detection from **verified** injection | 4.92 s ² | 4.81 s (4.92 − 0.11 s injection, from the run timeline) |
| Rollback → first fresh data | 0.68, 0.69, 1.86, 1.89 s | 0.67 s (Day 1: 0.53–1.59 s) |
| Rollback → recovery verified | 2.49–3.70 s | 3.06 s (Day 1: 2.64–3.73 s) |
| rFMS latency p95 during fault | 98–125 ms | 109 ms |

¹ Runs 1–3 predate the `injection_latency_s` field; their values are taken from the run timelines
(`fault_active` − `injecting`). Run 4 records it directly.
² Recorded in run 4 only (`fault_active.detail.exit_code`).

### What the measurements say
- **Hypothesis confirmed:** a crashed forwarder is as silent as a paused one: the rFMS API answered HTTP 200 on every
  fault-window poll while `createdDateTime` stopped.
- **Finding (verified):** `fms-forwarder` does not handle SIGTERM. `docker stop` always waits the full grace period
  and then kills it (exit code 137), and telemetry kept flowing for 2.6–3.1 s after the stop command. Detection
  measured from the command is therefore ≈ 2.4 s longer than for a pause; from verified injection it is the same.
- **Hypothesis not supported:** "after a cold start, fresh data returns later than after unpause". With 4 runs the
  ranges overlap (0.68–1.89 s vs 0.53–1.59 s). No measurable difference at this sample size.

## Guardrails: what was verified live and what was not

| Guard | Live | Unit tests |
|---|---|---|
| Blast radius (monitor thread + final check) | **Tripped live** (run `…4aa9f7`): Grafana restart detected from its changed start time even though its status stayed `running` | yes |
| Error rate | Armed on every run, never tripped live: no experiment so far makes the API return errors (planned: `dependency_failure`) | yes |
| Latency p95 | Armed on every run (threshold 439 ms = 5 × stored baseline p95 87.9 ms), never tripped live | yes |
| Kill switch starts a stopped target | **Verified live** (run `…6f6559`) | — (bash) |
| Startup recovery starts a stopped target | **Verified live** (run `…b10eff`) | yes |

## Re-verification after the safety-review fixes (same day, final code on this branch)

| Run | Experiment | Injection latency | TTD from command / from verified injection | HTTP 200 | Rollback → first fresh / verified | Final blast check |
|---|---|---|---|---|---|---|
| `20261010T183644Z-8941cc` | service_crash 12 s | 2.41 s (exit code 137) | 6.73 s / 4.32 s | 100 % | 0.70 s / 2.56 s | clean |
| `20261010T183702Z-21c920` | telemetry_stall 12 s | 0.11 s | 5.02 s / 4.91 s | 100 % | 1.31 s / 3.19 s | clean |

Both COMPLETED. `RP_LIVE=1 pytest`: 143 passed. `kill-switch.sh` (with `--type container`) reported
`fms-forwarder already restored (running, not paused)`, exit 0.

---

## dependency_failure: stop/start `influxDB` with the expected-status rule (live, 2026-10-10 18:53–18:56 UTC)

Rule (approved by the lead): the declared HTTP 500 is tolerated during the fault window only; any other status, a
timeout or a lost connection aborts immediately; precheck and recovery stay strictly HTTP 200 + fresh advancing data.

| Run | Result | InfluxDB stop (injection latency, exit code) | Fault-window polls | Stored-data gap | Rollback → first fresh / verified | Final blast check |
|---|---|---|---|---|---|---|
| `20261010T185355Z-540224` | ABORTED, `observer_lost` | 0.39 s, exit 2 | 1 (no response within 2 s) | **4.51 s** (17 stored, median interval 0.501 s) | 1.12 s / 4.80 s | clean |
| `20261010T185440Z-2f33aa` | ABORTED, `observer_lost` | 0.42 s, exit 2 | 1 (no response within 2 s) | **2.01 s** (15 stored) | 0.63 s / 1.94 s | clean |
| `20261010T185448Z-595a16` | ABORTED, `observer_lost` | 0.45 s, exit 2 | 1 (no response within 2 s) | **4.01 s** (13 stored) | 0.66 s / 2.54 s | clean |
| `20261010T185518Z-219f1c` | LATCHED_UNSAFE (by design) | 0.44 s | — | — | — | — |

The last run is the crash test: the backend was hard-killed about 0.4 s after the stop. `kill-switch.sh` printed
`started influxDB` (exit 0); the next backend start latched.

### Hypotheses vs measurements
1. **"fms-server answers HTTP 500, a visible failure": partly wrong.** The API does not fail *fast*. In every run the
   first fault-window poll got **no response within the 2 s observer timeout**, and under the agreed rule each run
   aborted on that poll. fms-server logs show each request first spends about 2 s failing to connect to the stopped
   InfluxDB (`isahc … failed to connect to the server`) and only then answers 500. Two independent
   `curl -m 10` requests sent during runs 2 and 3 got **HTTP 200 after 3.17 s**: they blocked until InfluxDB was back
   (after our rollback) instead of failing. Clients with timeouts under ~2–3 s therefore see **timeouts**, not 500s.
2. **"Statuses produced during the outage are lost": verified.** fms-consumer logged
   `failed to write data to influx`, and the stored-data window query shows gaps of 2.0–4.5 s in a stream that is
   otherwise stored every 0.501 s. There is no buffering or backfill.
3. **"No other container changes state": verified for these runs.** The final blast check was clean in all 3.

### Incident during the crash test (recorded, including an operator error)
- **18:55:30:** the kill switch started InfluxDB. It reported healthy, but fms-server kept answering HTTP 500 with
  `failed to resolve host name` until **18:55:48**, about 18 s after InfluxDB was running. That outage had lasted
  about 10 s, longer than the ~2.5 s orchestrated runs. Root cause not verified (DNS caching in fms-server's HTTP
  client is a candidate). InfluxDB's network attachment was correct (`influxdb` alias present).
- **Operator error (Claude):** `reset` was run while the API was still answering 500, because `reset` only checks
  container states. An emergency stop was engaged as soon as this was noticed. The API recovered on its own by
  18:56:06; reset was redone only after 12/12 fresh observations. Proposed fix: `reset` should require fresh,
  advancing telemetry, not only healthy containers (awaiting the lead's decision).

### Also resolved today: the 1.0 s vs 500 ms cadence question
InfluxDB stores a status every **0.501 s** (window query: 56 statuses, interval 0.499–0.503 s), so fms-forwarder
honours `TIMER_INTERVAL=500ms`. The `latestOnly=true` read returns a new `createdDateTime` only about every 1.0 s
(8 samples at 0.25 s showed 4 distinct values). The 1.0 s cadence is therefore produced by fms-server's
`latestOnly` read path, not by the data. Exactly why that query skips every other status is not verified.

---

## dependency_failure with the 5 s response envelope (approved option b), 2026-10-10 19:28–19:31 UTC

Envelope for this experiment, fault window only: observer timeout 5 s (precheck/recovery keep 2 s), latency cap
4.5 s, HTTP 500 expected, any other status or a lost poll aborts. Duration must exceed 5 s.

| Run | Result | Fault-window polls | After rollback (InfluxDB running again) | Stored-data gap |
|---|---|---|---|---|
| `20261010T192839Z-179795` | **LATCHED_UNSAFE**: recovery not verified in 30 s | 1: **no answer within 5 s** (`observer_lost`) | HTTP 200 with `createdDateTime` **frozen at the pre-fault value for ~27 s**: fms-consumer logged `failed to resolve host name` until 19:29:15, while fms-server served old data | — (recovery not verified) |
| `20261010T193003Z-e2eacd` | COMPLETED (`duration_elapsed`) | 6 × **HTTP 500**: the first after 3.97 s, then in ~5 ms each | **HTTP 500 for ~22 s** (fms-server `failed to resolve host name`), then fresh: first fresh +22.4 s, verified +24.2 s | 4.0 s (70 stored) |

### What this shows (measured)
1. **An InfluxDB outage is not a fast failure.** Requests first block (3.97 s, and over 5 s in run 1), then fail fast
   (HTTP 500 in ~5 ms) once name resolution fails.
2. **Recovery takes 20–27 s longer than the dependency's own restart.** After InfluxDB is back, fms components keep
   failing to resolve its host name for 22–27 s. Run 1 hit the **consumer**: a *silent* failure, HTTP 200 with data
   frozen. Run 2 hit the **server**: a *visible* failure, HTTP 500.
3. **ResiliencePilot's recovery rule caught the silent case.** In run 1 every container was running and the API
   answered 200, yet recovery was not declared and the run latched. A container- or HTTP-based health check would
   have called it recovered.
4. **Data loss happens in the fast-fail phase only.** Writes made during the blocking phase were delayed, not
   dropped (12 s fault → 4.0 s stored gap in run 2).
5. **Hypothesis, not verified:** the HTTP client (isahc/libcurl) caches failed DNS lookups. This would also explain
   the earlier incident (fms-server, ~18 s).

### Telemetry-checked reset (approved), verified live
After run 1 latched, `reset` succeeded only once 3 consecutive FRESH polls with an advance were seen; the check is
recorded in the reset history. The incident case (containers healthy, API answering 500) is covered by
`test_reset_refused_while_api_answers_500_with_healthy_containers`.

## Milestone-2 top-up runs (2026-10-10 20:36–20:38 UTC, macOS, envelope D-017 unchanged)
Goal: 3 completed `dependency_failure` runs. Before the runs: dashboard emergency stop (engaged 17:12 UTC) cleared by a
logged, telemetry-checked reset (operator nishtrip14); fresh baseline `baseline-20261010T203606Z.json` (30/30 FRESH,
latency p95 83 ms). All three: rollback verified, recovery verified, final blast check clean.

| Run | Result | Fault-window polls | After rollback | Stored-data gap | Score |
|---|---|---|---|---|---|
| `20261010T203616Z-596e45` | ABORTED, `observer_lost` | 1: no answer within 5 s | 3 × HTTP 500, then fresh; verified +4.6 s | 7.5 s | 69.6 (partial) |
| `20261010T203712Z-08b9dd` | **COMPLETED** | 14 × HTTP 500 (first in 158 ms) | HTTP 500 for ~18 s (server-side, visible); verified +20.5 s | 10.1 s | 63.2 |
| `20261010T203817Z-01d396` | ABORTED, `observer_lost` | 1: no answer within 5 s | **HTTP 200 + stale data on 42 polls (~21 s, consumer-side, silent)**; verified +27.1 s | **30.7 s** | 24.6 (partial) |

Outcome: **2 of the 3 required completed runs** (`e2eacd`, `08b9dd`). Under the 5 s envelope, 3 of the 5 envelope-era
runs aborted on the first fault poll because fms-server blocked longer than 5 s. Reaching 3 completed runs either
needs more attempts (about 2 in 5 complete) or a wider envelope, which is a safety-config decision for the lead
(D-017). Both recovery modes from Day 2 reproduced: server-side visible 500s and consumer-side silent stale data.
