# Handoff

_Updated: 2026-10-11 (07:55 IST, feat/scene-3d M2). **Code frozen for the demo** (lead's instruction). `main`: PRs #7 and #22–#30 merged (PR #29
approved by the lead) + the final count update. PR #8 still open (older)._

## Optional visualization: branch `feat/scene-3d` (in progress, NOT merged; merge only with the lead's approval)
- Scope: `docs/scene/` (page `index.html` + vendored `three.r160.min.js`, MIT licence at its top; `screenshots/`)
  and `scripts/build_scene_data.py` (+ `docs/TEAM_FORM_INFO.md` third-party list). No backend/config/test changes.
- Page: double-click `docs/scene/index.html` (file://, offline, no fetch/XHR, read-only replay of committed runs).
  Regenerate embedded data: `python3 scripts/build_scene_data.py` (`--check` verifies it is current).
- **M1 done (core scene)**: 8 buildings + roads from `config/dependency_map.json`, instanced packets coloured by
  recorded freshness, main truck driven by recorded `createdDateTime` (loop path illustrative: no gnssPosition in
  records), ghost truck (illustrative), timeline (play/pause/restart, 1x/2x/4x, scrubber, event markers), run selector
  (37 runs: telemetry_stall / service_crash / dependency_failure / kill-switch run `6f6559` / guardrail-demo), safety
  booth with inspector (config/safety.json checks, approver from the run record). Screenshots `docs/scene/screenshots/m1-*`.
- **M2 done (inspector)**: hover tooltip + click side panel for every building (container, image, ports, role, edges with
  confidence, allowlist, blast radius, state at the replay moment), road (protocol, direction, evidence level, file:line),
  packet (recorded observation fields; signal values "not recorded"), main/ghost truck, loop road, booth, and an
  AI-advisory kiosk / "Tolerances & AI" panel (thresholds from config, AI boundary from docs/AI_LAYER.md, model and
  validator counts from `reports/day2/ai_calls/20261010T233726Z.json`). Every fact shows `source: <path>`.
  Screenshots `docs/scene/screenshots/m2-*`. URL hash for reproducible views: `#run=<id>&t=<s>&play=0&sel=building:fms-server&tip=road:2`.
- Checks done: `python3 scripts/build_scene_data.py --check` (data current + static offline scan: PASS; negative-tested
  with injected fetch/XHR/CDN script/CSS url); Chrome net-log while loading from a fresh clone: only browser-internal
  requests (initiator "not an origin"), none from the page; WebGL-off fallback shows the explainer link; 390 px layout
  checked in an iframe; fresh clone needs `git submodule update --init` only to REBUILD data, not to view the page.
  `git diff --stat main`: only docs/scene/, scripts/build_scene_data.py, docs/TEAM_FORM_INFO.md, this file.
  Tests unchanged: 317 passed, 11 skipped (macOS).
- Next: STOP after M2 and report to the lead. M3/M4 only with explicit approval. PR open, do not merge without approval.

## RESUME HERE
- Status: `docs/coordination/CURRENT_STATUS.md`; requirements: `docs/REQUIREMENTS_TRACE.md` (final); guide:
  `docs/PROJECT_A_TO_Z.md` (refreshed).
- Tests on `main`: 317 passed, 11 skipped; `RP_LIVE=1`: 320 passed, 8 skipped; `demo-day1.sh` exit 0.
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
