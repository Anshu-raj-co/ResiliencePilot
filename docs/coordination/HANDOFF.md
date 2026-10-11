# Handoff

_Updated: 2026-10-11 ~06:15 IST. **Code frozen** (lead's instruction); docs only from here. `main` = PRs #7, #22–#28
merged + this docs PR. **Open: PR #29** (latency guardrail demo: tighten-only check vs implicit defaults, profiles may
shorten `max_duration_s`; live trip `20261011T002904Z-8ad2b8`), waiting for the lead. PR #8 still open (older)._

## RESUME HERE
- Status: `docs/coordination/CURRENT_STATUS.md`; requirements: `docs/REQUIREMENTS_TRACE.md` (final); guide:
  `docs/PROJECT_A_TO_Z.md` (refreshed).
- Tests on `main`: 315 passed, 11 skipped; `RP_LIVE=1`: 318 passed, 8 skipped; `demo-day1.sh` exit 0.
- If PR #29 is merged: update the test counts to 317 / 320 and mark req 4 DONE in the trace.
- Controller READY; local stack running.

## Earlier (before 2026-10-11 05:00 IST)
- **Submission pack (branch `docs/submission-pack`):** `README.md` (final, screenshot + scores),
  `docs/ARCHITECTURE_OVERVIEW.md`, `docs/demo/DAY2_DEMO_SCRIPT.md` (3 min), `docs/TEAM_FORM_INFO.md` (team fields
  marked [TO FILL]: only `Anshu-raj-co` and `nishtrip14` are known from git), `reports/day2/findings.md` (21 runs).
- **D-022 (PR #20):** envelope 8 s / 7.5 s; 3rd completed dependency_failure run `748875` (envelope not exercised;
  only first ~4 s of the window polled). AI: 14B analyst times out on this Mac; config stays 7B, 240 s.
- **Live runs (M2 top-up):** 3 `dependency_failure` attempts at 20:36–20:38 UTC: `08b9dd` COMPLETED; `596e45`,
  `01d396` ABORTED `observer_lost` (first fault poll blocked > 5 s envelope). All rollback + recovery verified, blast
  clean. Completed dependency_failure runs: **2 of 3** (`e2eacd`, `08b9dd`). 3 of 5 envelope-era runs abort on the
  first poll; reaching 3 needs more attempts or a wider envelope (D-017, lead's call). Details:
  `reports/day2/README.md` (last section). Emergency stop from the dashboard (17:12 UTC) was cleared by a logged reset
  first, with the user's approval.
- **Phase E dashboard:** `GET /api/insights?source=local|day2` (read-only; fixed names, never a path; built by
  `insights.py` from the same functions as `rp score/plan/findings`; planner uses the server's own catalog). Page:
  latest score with component bars + silent penalty, per-type medians, recent runs, planner proposals (text only, no
  button), findings with annotations; score refreshes when a run finishes. Screenshot `docs/demo/dashboard-day2.png`.
  Verified at 1200 px and 500 px (headless Chrome cannot render narrower; no page-level horizontal scroll at 500).
- Tests: 266 unit pass on macOS (+9 API insights).
- PR #7: decision renumbered to D-021 (`fc27d02`); still needs `main` merged in (DECISIONS.md conflict).
- Waiting: `qwen2.5-coder:14b` download (user); then set `model` in `config/ai.json` and rerun `rp advise --dir reports/day2`.

## Day 2 milestone 3: response envelope + telemetry-checked reset (branch `feat/day2-envelope-and-reset`, PR open)
- D-017 envelope (dependency_failure: 5 s timeout, 4.5 s latency cap, fault window only); D-018 reset needs 3 fresh,
  advancing polls. 184 unit tests.
- Live: envelope run 1 LATCHED (consumer DNS failures for ~27 s after influxDB returned → silent stale data);
  run 2 COMPLETED (500s; server DNS failures for ~22 s after return). Telemetry-checked reset verified live.

## Day 2 milestone 2: dependency_failure (MERGED, PR #16)
- Implemented the expected-status rule (D-015) and stored-data-gap evidence (D-016); 18 new tests; 158 unit tests.
- Live: 3 runs all ABORTED with `observer_lost` on the first fault poll. fms-server does not answer within 2 s while
  InfluxDB is down. Data loss verified (2.0–4.5 s gaps). Kill switch started influxDB with the backend dead.
- Incident: after the kill-switch restart, fms-server returned 500 ("failed to resolve host name") for about 18 s; I
  wrongly reset during it (reset checks containers only). Details in `reports/day2/README.md`.
- **Decisions pending (lead):** (1) how to observe slow-failing dependencies (per-experiment response envelope vs.
  keep the abort-on-first-poll result); (2) should `reset` require fresh telemetry?

## Day 2 milestone 1: service_crash + guardrails (MERGED, PR #15) (branch `feat/day2-service-crash`, PR open, awaiting approval)
- Implemented: `StopStartFault` (same-container-id rollback), guardrails (error rate, latency p95, blast-radius
  monitor + final check), kill switch and startup recovery restore stopped targets (start only for our own marker).
- Verified live (evidence `reports/day2/`, index `reports/day2/README.md`): 4 + 1 service_crash runs COMPLETED,
  telemetry_stall regression x2, blast-radius guard tripped live (Grafana restart), kill switch started a stopped
  target with the backend dead, startup recovery started it without the kill switch.
- NOT verified live: error-rate and latency guards tripping (no experiment produces errors/latency yet; unit-tested).
- Tests: 140 unit, 143 with `RP_LIVE=1`. Safety review: no high; M1–M4, L1–L4 fixed (`docs/testing/SAFETY_REVIEW.md`).
- Next: `dependency_failure` (stop influxDB) needs the expected-symptom rule (D-013 caps error tolerance at 0.5;
  an expected HTTP 500 must be modelled separately, not by raising tolerance). Then `sensor_freeze`.

## Where things are
- `main` = `843889a`: Day 1 complete, explainer (PR #4) and dashboard (PR #6) merged. Verified 2026-10-10 22:1x:
  `RP_LIVE=1 pytest` 70 passed; `scripts/demo-day1.sh` exit 0 (46 s).
- **Day 2 step 1 (cleanup): one PR per item, all open and awaiting the lead's merge approval:**
  | PR | Branch | Item |
  |---|---|---|
  | #10 | `fix/safety-file-retry` | Bounded PermissionError retry in `safety.py`; 12 tests; stress 40/40 with 87,261 concurrent `status()` calls; safety review: no high findings |
  | #9 | `fix/cli-utf8` | CLI forces UTF-8 stdout/stderr (piped output no longer crashes) |
  | #11 | `docs/readme-longpaths` | README `git -c core.longpaths=true submodule update --init` |
  | #12 | `docs/explained-refresh` | PROJECT_EXPLAINED §5, §6, §11, §12 |
  | this PR | `docs/day2-plan` | `docs/DAY2_PLAN.md` (plan only) + this handoff |
- Follow-ups noted by the safety review (pre-existing, fail-closed, not in scope): `latch()` in-memory fallback if
  its write fails; reorder `reset()` so files are deleted before writing the unlatched state.

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
After the lead merges PRs #9-#13: follow `docs/DAY2_PLAN.md`. Decision needed first: the per-experiment
"expected symptom" rule for `dependency_failure` (HTTP 500 currently trips `safety_threshold`).
Earlier plan (still valid), Phase 3 first task:
add `consumer_stall` (pause `fms-consumer`) and `forwarder_restart` (`docker stop`/`start`) to `config/experiments.json`,
`contracts/experiment_request.schema.json` and `ExperimentRequest`, with a `StopStartFault` adapter, the same
negative-path tests, and 3 repeated live runs each with TTD/TTR variability reported.
Open question to settle first: why `createdDateTime` advances at 1.0 s although `TIMER_INTERVAL=500ms` (recon §8).

## Uncommitted changes
- None after this checkpoint commit.
