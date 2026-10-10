# Resilience Score

`rp score` rates how the SDV stack behaved under one **verified** fault, on a 0–100 scale. It is computed only from a
stored run record (`backend/data/runs/*.json` or a committed copy). Nothing is estimated: a component the record does
not measure is shown as **n/m** (not measured) and left out of the weighted mean. Code: `backend/resiliencepilot/scoring.py`.

The score rates the **system under test** (the Eclipse SDV Fleet Management Blueprint), not ResiliencePilot.

## Formula

```
R = 100 × M × (1 − λ · P_silent)

M = Σ wᵢ·Sᵢ / Σ wᵢ      over the measured components i ∈ {D, R, G, B}
```

| Symbol | Component | Sub-score Sᵢ (clamped to 0–1) | Weight wᵢ |
|---|---|---|---|
| S_D | **Detection (MTTD)** | `1 − TTD / T_fault` | 0.25 |
| S_R | **Recovery (MTTR)** | `1 − TTR / T_rec`; 0 if recovery was not verified | 0.30 |
| S_G | **Data integrity (data gap)** | `1 − lost / T_fault` | 0.20 |
| S_B | **Blast radius** | `1 − changed / others` | 0.25 |
| P_silent | **Silent-failure penalty** | `silent polls / (fault + recovery polls)` | λ = 0.5 |

### Definitions (all from the run record)

- **T_fault**: the requested fault duration `request.duration_s`.
- **TTD**: time from the verified injection (`timeline: fault_active`) to the first fault-window poll that a client
  could act on: either a missing or non-200 response (**visible**), or HTTP 200 with data older than the stale
  threshold (**silent**). If no such poll exists, TTD is "not detected" and S_D = 0.
- **TTR**: `recovery.time_to_verified_s`, measured from the verified rollback to the third fresh advance of
  `createdDateTime` (safety rule 6). **T_rec** is `limits.recovery_timeout_s` (30 s).
- **lost**: `data_gap.max_gap_s − data_gap.median_interval_s`: the largest hole in **stored** telemetry for the run
  window beyond one normal interval (from `GET /rfms/vehiclestatuses?starttime&stoptime`, D-016). Only experiments
  that record `data_gap` (currently `dependency_failure`) measure G.
- **changed / others**: containers outside the experiment's `blast_radius` whose id, status or start time changed
  (guardrail trip or `final_blast_check`), divided by the number of containers outside the blast radius.
- **silent polls**: fault- and recovery-phase polls with `healthy_but_stale = true` (HTTP 200, stale data). The
  recovery phase is included on purpose: a stack that answers 200 with stale data *after* the fault was removed is
  failing silently too (run `20261010T192839Z-179795`: 47 of 51 polls).

## Why this shape

- **Weighted mean, not a product.** A product would turn a single 0 (for example, no data-gap measurement) into a
  total 0. The mean keeps the components readable on their own, and unmeasured components drop out instead of
  counting as 0 or 1.
- **Recovery weighs most (0.30).** It is the only component that is bounded by safety rule 6 and always measured
  after a verified rollback; failing to recover is the outcome an operator cares about most.
- **Silent failure is a multiplier, not a fifth term.** The project's core claim is that HTTP 200 can hide stale
  telemetry. A purely additive term could be outweighed by fast recovery; the multiplier caps a run that is silent on
  every poll at half of its score (λ = 0.5), whatever the other components say.
- **Normalised by the fault window.** TTD and data loss are measured against the requested fault duration so that a
  12 s and a 15 s run are comparable.

## What counts

| Run | Treatment |
|---|---|
| Fault verified, rollback verified, stopped by `duration_elapsed` | **Scored** and included in the per-type summary. |
| Stopped early by the system's own response (`observer_lost`, a guardrail) | **Scored but `partial`**: shown, excluded from the summary. A cut-short fault window makes TTD, data loss and recovery look better than a full-length fault would. |
| Stopped by `emergency_stop`, `cancelled`, or `error` | **Not scorable**: an operator or the tool ended it, not the system. |
| Rollback not verified, or the fault never verified active | **Not scorable**: the system state is unknown. |

## Known limits (read before comparing numbers)

- **S_D is dominated by the monitor's configuration.** For a silent stall, TTD ≥ the 5 s stale threshold plus up to
  one 1.0 s `createdDateTime` interval, so a 12 s stall cannot score above ~0.6 on detection. This rates how quickly
  the stack becomes *detectably* wrong under this monitor; it is not a property of the stack alone.
- **G is measured for `dependency_failure` only.** For a pause or a stopped forwarder, telemetry is not produced at
  all during the fault (no backfill), so the gap equals the fault window by construction and is not measured.
- **The blast-radius guard cannot tell a cascade from an operator action.** Run `20261010T182500Z-4aa9f7` tripped it
  because Grafana was restarted on purpose to test the guard; it is `partial` and excluded from the summary.
- **Small samples.** Day 2 has n = 2 / 5 / 1 full-window runs for telemetry_stall / service_crash /
  dependency_failure. Report the range with the median.

## Day-2 results (committed runs in `reports/day2/`)

Command: `rp score --dir reports/day2`. Full output in `reports/day2/scores.json`.

| Experiment | Full-window runs | Median | Range | What drives it |
|---|---|---|---|---|
| telemetry_stall | 2 | 62.2 | 60.9–63.5 | Silent on 10–14 polls per run; recovery fast (S_R ≈ 0.9) |
| service_crash | 5 | 65.1 | 61.9–65.3 | Same silent symptom as the stall; recovery fast (S_R ≈ 0.9) |
| dependency_failure | 1 | 61.6 | 61.6 | Failure is visible (no silent polls) but recovery takes 24.2 s (S_R = 0.19) and 3.5 s of stored data is lost |

The lowest score is the partial run `20261010T192839Z-179795` (14.3): rollback verified, but the stack answered
HTTP 200 with stale data on 47 of 51 polls and did not recover within 30 s.
