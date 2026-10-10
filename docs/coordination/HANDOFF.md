# Handoff

_Updated: 2026-10-10 ~15:20 IST, Day-1 wrap-up (presentation + judge report)_

## Where things are
- Branch `wip/day1-recon` (pushed). Last known-good on main: `401a23d`.
- Phases 0–2 done and verified live; see `CURRENT_STATUS.md` for the acceptance checklist and `docs/recon.md` §8 for numbers.
- Safety review done and fixed (`docs/testing/SAFETY_REVIEW.md`). Demo: `docs/demo/DAY1_DEMO.md`, `scripts/demo-day1.sh`.
- PR #1 merged into `main` (`401a23d`). Wrap-up (dependency map, load conditions, impact view, judge report) is in PR #2 from `wip/day1-recon`; merge is left to the engineering lead.

## Where the evidence lives
| What | Where |
|---|---|
| Dependency map (data) | `config/dependency_map.json` (edges, protocols, file:line evidence, confidence); prose in `docs/recon.md` §3–4 |
| Show it | `python -m resiliencepilot deps` (adds a live shared-network check) |
| Baselines (all) | `backend/data/baselines/*.json` (gitignored, local) |
| Baselines (committed) | `reports/day1/baseline-20261010T094007Z.json` (reference, with `load_conditions`), `reports/day1/baseline-20261010T084740Z.json` (first, no load conditions) |
| Show one | `python -m resiliencepilot show-baseline [FILE]` |
| Run records | `backend/data/runs/` (local); 12 real runs copied to `reports/day1/`; `python -m resiliencepilot show <run_id>` draws the impact chart |
| Judge report | `docs/demo/DAY1_REPORT.md` (every number taken from the files above) |

## How to resume
```bash
git fetch && git checkout wip/day1-recon
git submodule update --init                    # fresh clone only (blueprint pinned at 3d514ac72a)
./scripts/stack-up.sh && ./scripts/stack-status.sh
cd backend && python -m venv .venv && .venv/Scripts/python -m pip install -e ".[test]"
.venv/Scripts/python -m pytest -q              # expect 49 passed
RP_LIVE=1 .venv/Scripts/python -m pytest -q    # expect 52 passed
.venv/Scripts/python -m resiliencepilot status # must be READY; if LATCHED_UNSAFE read the reason, check stack, then reset
```
If `docker info` says "Docker Desktop is unable to start": quit Docker Desktop fully and relaunch it.

## Next concrete task
Day 1 is complete. Next session = Phase 3 (`docs/ROADMAP.md`), first task:
add `consumer_stall` (pause `fms-consumer`) and `forwarder_restart` (`docker stop`/`start`) to `config/experiments.json`,
`contracts/experiment_request.schema.json` and `ExperimentRequest`, with a `StopStartFault` adapter, the same
negative-path tests, and 3 repeated live runs each with TTD/TTR variability reported.
Open question to settle first: why `createdDateTime` advances at 1.0 s although `TIMER_INTERVAL=500ms` (recon §8).

## Uncommitted changes
- None after this checkpoint commit.
