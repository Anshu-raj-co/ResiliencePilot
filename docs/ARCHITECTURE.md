# Architecture (Day 1)

```
                 ┌──────────────── Docker Compose project "resiliencepilot-sdv" (local sandbox) ───────────────┐
                 │ csv-provider → databroker → fms-forwarder ─zenoh→ fms-zenoh-router → fms-consumer → InfluxDB │
                 │                                   ▲ pause/unpause                                  │         │
                 │                                   │ (allowlisted)                     fms-server (rFMS API)  │
                 └───────────────────────────────────┼────────────────────────────────────────┬───────────────┘
                                                     │ docker CLI                             │ HTTP GET (read-only)
┌──────────────────────── ResiliencePilot backend (Python) ──────────────────────────────────┼───────────────┐
│  docker_cli.py ◀── faults.py (PauseFault, Watchdog) ◀── orchestrator.py ──▶ sdv_adapter.py (RfmsObserver) │
│                                   ▲                         │   ▲                     │                    │
│                         safety.py (SafetyController)        │   └── telemetry.py (FreshnessTracker)       │
│                         validate · allowlist · lock ·       ▼                                              │
│                         latch · marker · emergency stop   evidence.py (JSON run records, baselines)        │
│                                   ▲                                                                        │
│  __main__.py (operator CLI)  ─────┘            scripts/kill-switch.sh (independent of backend)             │
└────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Components → files

| Brief component | Day-1 implementation |
|---|---|
| SDV adapter | `sdv_adapter.py` — read-only `GET /rfms/vehiclestatuses?latestOnly=true` |
| Dependency model | `docs/recon.md` §3–4 (verified from source); machine-readable model is Phase 3+ |
| Telemetry verifier | `telemetry.py` — age + progression, `healthy_but_stale` flag |
| Baseline collector | `baseline.py` |
| Experiment orchestrator | `orchestrator.py` — state machine, rollback in `finally`, recovery verification |
| Safety controller | `safety.py` |
| Fault adapters | `faults.py::PauseFault` (telemetry_stall) |
| Watchdog / emergency stop | `faults.py::Watchdog`; `SafetyController.emergency_stop`; `scripts/kill-switch.sh` |
| Recovery verifier | `ExperimentRunner._finish` (independent of the adapter's own claim) |
| Evidence store | `evidence.py` → `backend/data/` (gitignored); curated real runs in `reports/day1/` |
| AI analyst, frontend, report exporter | Not started (Phases 4–5) |

## Experiment state machine

```
VALIDATED → PRECHECK ──(stale / not sandbox / stop)──────────────────────────────▶ ABORTED (no fault)
               │ 3 fresh samples incl. ≥1 advance
               ▼
          INJECTING (marker written, watchdog armed, docker pause, verify Paused=true)
               │                     └─(not verified)─┐
               ▼                                      │
            ACTIVE ── duration │ emergency stop │ observer lost │ non-200 │ watchdog │ exception
               ▼                                      ▼
         ROLLING_BACK (idempotent, serialised with watchdog) ──(not verified)──▶ LATCHED_UNSAFE
               ▼
      VERIFYING_RECOVERY ──(criteria not met in 30 s)──────────────────────────▶ LATCHED_UNSAFE
               ▼
          COMPLETED (fault verified)  /  ABORTED (fault never verified)
```

Hard bound on fault time: `duration_s (≤30) + watchdog_grace_s (5)`, enforced by the watchdog thread unless the
process itself dies (then: kill switch / startup recovery, see DECISIONS D-011).

## Interfaces
JSON Schemas in `contracts/`: `experiment_request`, `observation`, `experiment_run`. The pydantic model
`safety.ExperimentRequest` is the enforcement point (strict types, `extra="forbid"`).
