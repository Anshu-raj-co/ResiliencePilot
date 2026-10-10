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
