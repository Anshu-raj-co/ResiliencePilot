# Requirements trace

Verification only: no code was changed. Repository state: `main` at `2fd518d` (PR #20; PR #21 included).
Checked on 2026-10-11 on macOS 27.0.1, Apple Silicon (arm64), Docker 29.7.2, Python 3.13.

Status meanings:
- **DONE**: the requirement is met and the evidence is in the repo or was reproduced during this check.
- **PARTIAL**: part of the requirement is met; the note says which part is missing.
- **MISSING**: no evidence found.

Run IDs refer to committed records in `reports/day1/` or `reports/day2/`. The two "fresh-clone" runs were produced
during this check and exist only in the scratch clone; they are not committed.

## Summary

| # | Requirement | Status |
|---|---|---|
| 1 | AI planner ranks and selects experiments from the dependency map, with a reason for each | PARTIAL |
| 2 | 3+ failure types, with errors, slowdowns and recovery time recorded | DONE |
| 3 | AI explains findings, ranks by severity, recommends fixes, gives a resilience score; formula documented | PARTIAL |
| 4 | Automatic stop on error rate, latency and blast radius, each tested live | PARTIAL |
| 5 | Semi-automatic dependency mapping | DONE |
| 6 | Kill switch | DONE |
| 7 | Human approval before execution | DONE |
| 8 | Rule-based fallback when no model is available | DONE |
| 9 | Clean run from scratch | PARTIAL (fails as written on this Mac; see section 9) |

## Detailed trace

### 1. AI planner ranks and selects experiments from the dependency map, with a reason for each: PARTIAL

| Part | Status | Evidence |
|---|---|---|
| Ranking uses the dependency map | DONE (rule-based) | `backend/resiliencepilot/planner.py` (`_dependents(dep_map)`, risk = 0.5 × dependents + 0.3 × latch rate + 0.2 × severity); `config/dependency_map.json`; test `test_never_run_shared_store_ranks_first_with_exact_factors`, `test_candidates_come_only_from_the_allowlist` (`backend/tests/test_planner_findings.py`) |
| A reason for each entry | DONE | Rule-based reasons: `reports/day2/findings.md` ("Next experiments"), `reports/day2/ai_advice_offline.md`. AI reasons: `reports/day2/ai_advice_qwen2.5-7b.md` lines 5–17 |
| The AI ranks | DONE, limited | The AI may only reorder the rule-based candidates (`ai.py` `validate_plan`, exact permutation); tests `test_valid_reorder_is_accepted_but_commands_come_from_the_rule_based_plan`, `test_invalid_plans_fall_back_to_the_rule_based_order`. In the one stored real call, the AI order equals the rule-based order (`ai_advice_qwen2.5-7b.md`: ranks 1, 2, 3 unchanged) |
| The planner selects | MISSING | Nothing selects an experiment automatically. The planner only proposes; a human picks and approves (`rp run --approved-by`). The dashboard Start button runs one fixed template (`api.py:35`, telemetry_stall), not the planner's top choice; test `test_page_shows_proposals_without_any_way_to_run_them` |

Model and logging: see [AI model, calls and logging](#ai-model-calls-and-logging).

### 2. 3+ failure types with errors, slowdowns and recovery time recorded: DONE

| Part | Evidence |
|---|---|
| 3 failure types | `config/experiments.json`: `telemetry_stall` (docker pause), `service_crash` (docker stop/start fms-forwarder), `dependency_failure` (docker stop/start influxDB). Tests: `test_safety.py`, `test_service_crash.py`, `test_dependency_failure.py` |
| Completed live runs per type | telemetry_stall `20261010T181417Z-c1ab0d`, `20261010T183702Z-21c920` (+ Day-1 runs); service_crash `20261010T181441Z-920732`, `…181503Z-31eafe`, `…181525Z-8fa234`, `…182331Z-b89ae0`, `…183644Z-8941cc`; dependency_failure `20261010T193003Z-e2eacd`, `20261010T203712Z-08b9dd`, `20261010T211735Z-748875` |
| Errors recorded | Each poll stores `http_status` and `error` (`observations[].observation`); run summary stores `http_error_rate_during_fault`. Example `20261010T193003Z-e2eacd`: error rate 1.0, 6 × HTTP 500 |
| Slowdowns recorded | Each poll stores `latency_ms`; run summary stores `latency_p95_during_fault_ms`. Example `20261010T193003Z-e2eacd`: p95 3966.72 ms (baseline p95 87.87 ms) |
| Recovery time recorded | `recovery.time_to_first_fresh_s`, `recovery.time_to_verified_s` in every run with verified rollback. Example `20261010T193003Z-e2eacd`: 24.247 s |

Note: slowdowns are **measured**, not **injected**. None of the 3 failure types injects latency (no network-degradation
experiment; `docs/DECISIONS.md` D-004, `docs/recon.md` §5).

### 3. AI explains findings, ranks by severity, recommends fixes, gives a resilience score; formula documented: PARTIAL

| Part | Status | Evidence |
|---|---|---|
| Explains findings | PARTIAL | AI analyst: `ai.py` `ai_analyse`. In the stored real call, **1 of 7** AI explanations passed validation; 6 were rejected and replaced by rule-based text (`reports/day2/ai_advice_qwen2.5-7b.md`; `docs/AI_LAYER.md` "Measured on 2026-10-11"). Tests: `test_analyst_accepts_valid_items_and_backfills_the_rest_from_rules`, `test_grounding_rejects_the_first_live_run_mistakes`, `test_grounding_accepts_evidence_consistent_causes` |
| Ranks by severity | DONE, but rule-based, not AI | `backend/resiliencepilot/findings.py` (fixed rule order, "most severe first"); output `reports/day2/findings.md`; test `test_findings_rules_fire_on_their_evidence`. The AI does not rank findings |
| Recommends fixes | DONE, mostly rule-based | Playbooks (verify / mitigate / fix) per finding in `reports/day2/ai_advice_qwen2.5-7b.md` and `ai_advice_offline.md`. 6 of 7 playbooks in the real-call report come from the rule-based knowledge base (`ai.py` `KB`) |
| Gives a resilience score | DONE, but deterministic, not AI | `backend/resiliencepilot/scoring.py`, `rp score`; output `reports/day2/scores.json`; tests in `backend/tests/test_scoring.py` (e.g. `test_full_window_stall_exact_values`, `test_golden_committed_dependency_failure_run`). The AI does not compute or adjust the score |
| Formula documented | DONE | `docs/SCORING.md` (formula, weights, definitions, limits) |

Model and logging: see [AI model, calls and logging](#ai-model-calls-and-logging).

### 4. Automatic stop on error rate, latency and blast radius, each tested live: PARTIAL

| Guard | Implemented | Unit-tested | Tripped in a live run |
|---|---|---|---|
| Error rate | `guardrails.py`; stop reason `guardrail_error_rate` (`orchestrator.py:254`) | `test_error_rate_trips_and_rolls_back`, `test_error_rate_guard_zero_tolerance_trips_on_first_error`, `test_error_rate_tolerance_is_per_experiment` | **No.** No committed run has stop reason `guardrail_error_rate` |
| Latency | `guardrails.py`; stop reason `guardrail_latency` (`orchestrator.py:255`) | `test_latency_p95_trips_and_rolls_back`, `test_latency_guard_needs_min_samples_then_uses_rolling_p95`, `test_latency_threshold_prefers_the_stored_baseline` | **No.** No committed run has stop reason `guardrail_latency` |
| Blast radius | `guardrails.py`; stop reason `guardrail_blast_radius` (`orchestrator.py:240`) | `test_blast_radius_breach_trips_rolls_back_and_latches`, `test_blast_radius_unobservable_trips`, `test_breach_after_the_fault_window_still_latches` | **Yes:** `20261010T182500Z-4aa9f7` (LATCHED_UNSAFE, `guardrail_blast_radius`; operator restarted grafana on purpose, `reports/day2/annotations.json`) |

All 33 committed run records were checked: their stop reasons are `duration_elapsed`, `emergency_stop`,
`observer_lost`, `error` and one `guardrail_blast_radius`. `docs/coordination/HANDOFF.md` also says: "NOT verified live:
error-rate and latency guards tripping".

Related: in `dependency_failure` runs, polls that got no answer in time stopped the run with `observer_lost`
(6 runs, for example `20261010T185355Z-540224`). That is the observer-loss rule, not the latency guard.

### 5. Semi-automatic dependency mapping: DONE

| Part | Evidence |
|---|---|
| Manual map with source evidence and confidence | `config/dependency_map.json` (edges, protocol, `file:line` evidence, confidence); prose in `docs/recon.md` §3–4 |
| Automatic discovery | `backend/resiliencepilot/discover.py`, `rp discover` (from `docker inspect`: env references, config files, `depends_on`) |
| Diff between the two | `rp discover` output "Compared with config/dependency_map.json"; committed output `reports/day2/discovery.json` (`diff_vs_dependency_map`) |
| Tests | `backend/tests/test_discover.py` (18 tests, e.g. `test_diff_against_map_classifies_every_edge`, `test_run_discovery_is_read_only_and_ignores_other_projects`, `test_secret_variables_are_never_inspected_or_stored`) |
| Reproduced during this check | Fresh clone, live stack: 8 containers, 8 edges; 6 matched, 1 direction differs (`fms-zenoh-router → fms-consumer`), 1 discovered only (`fms-forwarder → csv-provider`, low confidence) |

### 6. Kill switch: DONE

| Part | Evidence |
|---|---|
| Implementation | `scripts/kill-switch.sh` (works without the backend), `rp stop`, dashboard Kill (`POST /api/kill`) |
| Tests | `test_kill_switch_file_is_honoured`, `test_emergency_stop_rolls_back_early_and_blocks_new_runs_until_reset` (`test_safety.py`); `test_kill_from_second_client_rolls_back_and_blocks` (`test_api.py`); `test_startup_recovery_starts_a_stopped_target_and_latches` (`test_service_crash.py`) |
| Live, committed | `20261010T084814Z-b17ca6` (kill switch from a separate process; `docs/recon.md` §8); `20261010T182400Z-6f6559` (backend hard-killed, kill switch started fms-forwarder; `annotations.json`) |
| Reproduced during this check | Fresh-clone demo step 5: run `20261010T224656Z-4b9bbc` ABORTED `emergency_stop`, rollback verified, recovery verified 2.938 s after rollback; next run refused until reset |

### 7. Human approval before execution: DONE

| Part | Evidence |
|---|---|
| CLI | `approved_by` is required by `contracts/experiment_request.schema.json` (`additionalProperties: false`); `rp run` needs `--approved-by` |
| Tests | `test_malformed_or_invalid_requests_rejected_without_side_effects` rejects a missing or blank approver (`test_safety.py` lines 62–63) |
| Recorded | Every committed run record has `request.approved_by` (for example "nishtrip14 (D-022 8 s envelope, 3rd completed run)") |
| Dashboard | Browser `confirm("Approve and start? …")` before `POST /api/run` (`static/dashboard.html:217`); the recorded approver is the fixed name `dashboard-operator` (`api.py:35`), not the clicking person's identity. The confirm dialog itself has no automated test (browser JavaScript) |
| AI cannot start runs | `test_ai_module_cannot_reach_docker_shell_or_safety_state`; `test_page_shows_proposals_without_any_way_to_run_them` |

### 8. Rule-based fallback when no model is available: DONE

| Part | Evidence |
|---|---|
| Code | `ai.py` `ai_plan` and `ai_analyse` fall back on `AIUnavailable`/`ValueError`; `rp advise --offline` |
| Tests | `test_unreachable_or_broken_ollama_falls_back` (includes `httpx.ReadTimeout`), `test_offline_is_deterministic`, `test_cli_advise_offline_never_contacts_ollama`, `test_invalid_plans_fall_back_to_the_rule_based_order`, `test_malformed_analyst_items_are_rejected` |
| Committed output | `reports/day2/ai_advice_offline.md` ("provider: `rule-based`", "Fallback reason: AI disabled") |
| Live timeout fallback | Described in `docs/AI_LAYER.md` (`qwen2.5-coder:14b` timed out; every finding fell back with `ollama unreachable: ReadTimeout`). That report was **not** committed, so this is documented but not reproducible from repo files |

### 9. Clean run from scratch: PARTIAL

Done in a new folder from `git clone`, after `scripts/stack-down.sh` on the existing stack. README "Quick start" steps
were run as written. Results:

| Step (README) | Result |
|---|---|
| `git -c core.longpaths=true submodule update --init` | OK (blueprint at `3d514ac72a`). The README has no `git clone` line before it |
| `./scripts/stack-up.sh` | **FAILED:** `permission denied` (exit 126) |
| `bash scripts/stack-up.sh` (workaround to continue) | **FAILED:** `no matching manifest for linux/arm64/v8` while pulling `fms-server` and `fms-consumer` (exit 1); nothing was started |
| Start step only, using x86 images already cached on this Mac | **FAILED:** `bind: address already in use` on `127.0.0.1:55555` (databroker); 5 of 8 containers started |
| Retry after 15 s | Same failure |
| With a temporary compose file outside the repo moving databroker to host port 55557 | OK: 8 containers running; API HTTP 200 with fresh vehicle data |
| `./scripts/stack-status.sh` | Runs; but after the failed start it printed no containers and `HTTP 000` and still exited 0 |
| `python3.13 -m venv .venv`, `pip install -e ".[test]"` | OK |
| `pytest -q` | OK: 270 passed, 11 skipped |
| `rp status` | OK: READY |
| Demo: `RP_APPROVER=… scripts/demo-day1.sh` (via `bash`) | OK: exit 0 in 43 s. Run `20261010T224639Z-659a90` COMPLETED: stale detected 5.22 s after injection with HTTP 200, recovery verified 3.011 s after rollback. Run `20261010T224656Z-4b9bbc` ABORTED by kill switch, recovery verified 2.938 s after rollback. Next run refused until reset; then READY |
| `rp serve` | OK: dashboard HTTP 200; `/api/insights?source=day2` returns 21 runs |

Failures, exactly:
1. **Scripts not executable.** All 6 files in `scripts/` are committed with mode `100644`
   (`git ls-files -s scripts/`). On macOS/Linux, `./scripts/stack-up.sh` and every other `./scripts/…` command in
   the README fail with `permission denied`. This includes `scripts/kill-switch.sh` when called directly.
   `demo-day1.sh` calls the kill switch through `bash`, so the demo itself is not affected.
2. **No ARM images for two services.** `ghcr.io/eclipse-sdv-blueprints/fleet-management/fms-server:main` and
   `fms-consumer:main` publish only `linux/amd64` (`docker buildx imagetools inspect`). On an Apple Silicon Mac,
   `docker compose pull` fails and, because `_compose.sh` sets `set -e`, the stack never starts. It only works here
   because amd64 images were already cached. The README prerequisites do not mention this. `docs/recon.md` §3 checked
   amd64 manifests only.
3. **Port 55555 in use, and the documented remedy is not on `main`.** Port 55555 was already in use on this Mac. The
   process holding it could not be identified without administrator rights, and it was not a Docker container. The
   README says the port can be moved with `RP_DATABROKER_HOST_PORT` (PR #7), but that variable exists only on the
   unmerged branch `wip/databroker-port`; `git grep` on `main` finds it only in `README.md:74`.
4. **`stack-status.sh` does not report failure.** It exits 0 even with no containers and `HTTP 000`.

Not observed, but noted: `stack-up.sh` uses `sed -i 's/\r$//'` (GNU syntax). On macOS BSD `sed` this would fail if
a file had Windows line endings. That path was not triggered in this check.

## AI model, calls and logging

Applies to requirements 1 and 3.

| Question | Answer |
|---|---|
| Provider and model | Ollama, local only (`base_url` must be loopback; `test_provider_refuses_non_loopback_urls`). Model `qwen2.5:7b`, temperature 0, seed 42, timeout 240 s (`config/ai.json`). `qwen2.5-coder:14b` was also tried (`docs/AI_LAYER.md`) |
| Was a real model call made in the stored runs? | The experiment run records (`reports/*/2026*.json`) contain **no** AI calls; the AI runs separately over stored records (`rp advise`). One stored report comes from a real call: `reports/day2/ai_advice_qwen2.5-7b.md`. It shows provider `ollama:qwen2.5:7b` for both sections, the model's plan rationales, and the model's rejected root-cause texts. The report itself does not record the time of the call or the Ollama version; `docs/AI_LAYER.md` states "2026-10-11, Ollama 0.40.2" |
| Where are prompts logged? | **Nowhere.** Prompts are built at run time (`ai.py` `plan_prompt`, `analysis_prompt`; system prompts `PLAN_SYSTEM`, `ANALYSIS_SYSTEM` in source) and are not written to any file |
| Where are responses logged? | **Only partly.** The raw JSON response is not saved. `rp advise --out FILE` writes Markdown containing the accepted plan text, the accepted analyst items and, for each rejected item, the reason and the model's root-cause text. `rp advise --json` prints a structure with a `rejected` list to the terminal only |

## The DNS claim

| Claim | Where it appears | Evidence |
|---|---|---|
| fms-consumer / fms-server logged `failed to resolve host name` for 18–27 s after InfluxDB returned | `reports/day2/README.md:96, 119, 120`; `docs/recon.md:139`; `docs/coordination/HANDOFF.md:29-30, 36`; labelled **"verified"** in `ai.py:28` (`FACTS`) and in the rule-based texts (`ai.py:209, 216, 222`) | Quoted in prose only. **No raw `docker logs` output is committed**, and none of the 33 run records contains the text "resolve". It cannot be checked from repository files |
| Recovery is slow **because** components keep failing name resolution | `README.md:29` ("because fms components keep failing name resolution"), `README.md:35` | Rests on the log observation above. The timings are evidenced (for example `20261010T193003Z-e2eacd`: recovery 24.247 s); the cause is not |
| The HTTP client (isahc/libcurl) caches failed DNS lookups | `reports/day2/README.md:133`, `ai.py:32`, `reports/day2/ai_advice_*.md` | Already labelled **hypothesis, not verified** |

Recommended relabelling (not applied): state the log observation as "reported from container logs during the runs;
log excerpts not committed", or commit the log excerpts with timestamps next to the run IDs. Until then, the
`README.md:29` and `README.md:35` wording, and the "verified" label in `ai.py:28`, are stronger than the committed
evidence.

## Personal contact details and secrets

Scanned: every tracked file on `main` (`git grep`) and the git commit metadata. The blueprint submodule was checked
only for its Compose file.

| Item | Where | Type |
|---|---|---|
| Personal email `anshu1212raj@gmail.com` | Git commit author metadata (history, not files) | Personal contact |
| Personal email `vaibhavraghav14@gmail.com` | Git commit author metadata | Personal contact |
| Placeholder author `Your Name <your_email@example.com>` | Git commit author metadata (11 commits) | Misconfigured git identity, not personal |
| Local Windows path with username `C:/Users/anshu/OneDrive/Desktop/…` | `docs/demo/day1-demo-run.log:54, 169` | Personal path / username |
| Local macOS path with username `/Users/nishanttripathi/Desktop/…` | `reports/day2/scores.json:3` | Personal path / username |
| Names and GitHub handles | `docs/TEAM_FORM_INFO.md`; `request.approved_by` in run records ("Anshu (…)", "nishtrip14 (…)") | Names and handles, no contact details |
| InfluxDB demo credentials (`fms-demo` / `fms-demo-secret`, admin token `fms-backend-admin-token`), Grafana admin password `sdv` | Upstream submodule `fms-blueprint-compose.yaml:61-64, 110` | Public upstream sandbox defaults, local-only (acknowledged in `CLAUDE.md:61`) |
| Fake tokens such as `secret-influxdb-host-token` | `backend/tests/test_discover.py:39-40, 89` | Test fixtures, not real |

Not found: no `.env` files, API keys, GitHub tokens, private keys or phone numbers in tracked files; no email
addresses in tracked file contents; no credential values in `reports/day2/discovery.json`.
