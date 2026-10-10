# Handoff

_Updated: 2026-10-10 ~22:30 IST, Day 2 step 1 cleanup (one PR per item)_

## Where things are
- Branch `wip/day1-recon` (pushed). Last known-good on main: `401a23d`.
- Phases 0–2 done and verified live; see `CURRENT_STATUS.md` for the acceptance checklist and `docs/recon.md` §8 for numbers.
- Safety review done and fixed (`docs/testing/SAFETY_REVIEW.md`). Demo: `docs/demo/DAY1_DEMO.md`, `scripts/demo-day1.sh`.
- PR #1 merged into `main` (`401a23d`). Wrap-up (dependency map, load conditions, impact view, judge report) is in PR #2 from `wip/day1-recon`; merge is left to the engineering lead.

## Day-1 explainer (branch `feat/day1-explainer`, PR pending the lead's approval)
- `rp report [--run ID] [--out FILE]` → `reports/day1/explainer.html`: a standalone offline HTML page built only from
  `backend/data/runs/` (falling back to `reports/day1/`), the baselines in `reports/day1/` and `config/dependency_map.json`.
- Code: `backend/resiliencepilot/explainer.py` (read-only). No changes to safety, orchestrator or fault code.
  Tests: `backend/tests/test_explainer.py`.
- Verified by rendering in headless Edge: light and dark (screenshots inspected), and phone width measured in a 390 px iframe (scrollWidth 375 ≤ 390, no page-level horizontal scroll; diagram and chart scroll inside their boxes). Screenshot: `docs/demo/explainer-chart.png`.
- Regenerate after new runs; it picks the latest full-length COMPLETED run by default.

## Live dashboard (branch `feat/day1-dashboard`, in progress; PR pending the lead's approval)
- `rp serve [--port 8765]` serves the local API and dashboard on http://127.0.0.1:8765/ (bound to localhost only).
- `backend/resiliencepilot/api.py` calls only the CLI's own paths: `ExperimentRunner.start`, `SafetyController.emergency_stop`
  and `SafetyController.reset`. `POST /api/run` takes no parameters (fixed template, 12 s, approver `dashboard-operator`).
  Responses: 409 busy, 423 latched or stop engaged, 422 if any parameter is sent. Reset needs `{"confirm": true}`
  and is allowed only when blocked.
- Page: `backend/resiliencepilot/static/dashboard.html`. Tests: `backend/tests/test_api.py`.
- Evidence-store fix (`evidence.py`): retry `os.replace` on Windows PermissionError. A concurrent reader (the
  dashboard) made the final run save fail with "Access is denied". Safety/orchestrator/fault code is unchanged.
- Evidence store also retries reads (`load_run`) on Windows PermissionError.
- **Open defect in `safety.py` (not fixed: the safety code is off-limits without the lead's approval).** On Windows,
  `run.lock` / `active_fault.json` deletes and `safety_state.json` replaces fail with PermissionError when another
  thread or process has the file open. Seen as: a stale run.lock (stuck BUSY) and a spurious LATCHED_UNSAFE after a
  verified rollback. Both fail safe. The dashboard avoids it in-process: it reads status under `ctl._lock` and opens
  no controller files while its own run is active (40/40 clean API-suite runs). A CLI `rp status` in another
  terminal at the exact moment can still trigger it. Proposed fix: retry-on-PermissionError in `_atomic_write` and
  the unlink calls.
- Live end-to-end run from the page passed (run `20261010T102525Z-e0bb59`); see the PR description.

## Day 2 step 1 (cleanup) — in progress
- PR #6 (dashboard) was already merged (`843889a`). Verified on main: `RP_LIVE=1 pytest` 70 passed; `demo-day1.sh` exit 0, 46 s.
- `fix/safety-file-retry`: bounded retry (5 attempts, 20-50 ms) on PermissionError only, for safety-file replace/read/delete.
  Fails closed as before if retries fail. O_EXCL lock creation and all decisions untouched. Tests:
  `tests/test_safety_file_retry.py`. Stress: API suite 40/40 with a second process making 87,261 `status()` calls.
  Awaiting safety review + the lead's merge approval.

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
