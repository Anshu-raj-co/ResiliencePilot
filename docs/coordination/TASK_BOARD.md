# Task Board

Human teammates are not yet connected; roles below are placeholders (no identities assumed).

## Ownership & file boundaries

| Role | Owns (files) | Depends on |
|---|---|---|
| H1 Lead / integration | `contracts/`, `docs/`, `CLAUDE.md`, PR review, demo | all |
| H2 SDV integration | `config/compose/`, `scripts/stack-*.sh`, `backend/resiliencepilot/sdv_adapter.py`, `docs/recon.md` | blueprint |
| H3 Safety / faults | `backend/resiliencepilot/safety.py`, `faults.py`, `docker_cli.py`, `config/safety.json`, `backend/tests/test_safety*.py` | contracts, H2 |
| H4 Telemetry / evidence | `backend/resiliencepilot/telemetry.py`, `baseline.py`, `evidence.py`, `backend/tests/test_telemetry*.py` | H2 |
| H5 AI / UI / QA | `frontend/`, `backend/resiliencepilot/analyst/` (Phase 4+), e2e tests | contracts, H3, H4 |

## Day 1 tasks

| # | Task | Owner role | Status | Acceptance |
|---|---|---|---|---|
| 1 | Recon of blueprint + data path | H2 | DONE | `docs/recon.md` with [V]/[I]/[Q] labels |
| 2 | Run stack locally (bridge override, CRLF fix) | H2 | DONE | 8 containers up; `createdDateTime` advancing via rFMS API |
| 3 | Contracts (experiment request, observation, run record) | H1 | TODO | JSON Schemas in `contracts/` |
| 4 | Telemetry verifier + baseline collector + tests | H4 | TODO | stale/non-advancing detection unit-tested; real baseline file |
| 5 | Safety controller, watchdog, kill switch, recovery gate + negative tests | H3 | TODO | all Phase 2 negative cases tested |
| 6 | Telemetry-stall experiment (pause forwarder) live run | H3/H4 | TODO | impact + rollback + verified recovery recorded |
| 7 | Independent safety review (subagent) | H1 | TODO | findings addressed or documented |
| 8 | Day 1 demo script | H1 | TODO | `docs/demo/DAY1_DEMO.md` runnable in ~2 min |

Phases 3–6: see `docs/ROADMAP.md`.
