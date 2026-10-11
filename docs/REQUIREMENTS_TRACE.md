# Requirements trace

Verification of each Bosch requirement against evidence in the repository. Final regeneration on 2026-10-11 (code
frozen). State checked: `main` after PRs #7 and #22–#28, plus **PR #29 (open, awaiting the lead's approval)** where
noted. Host: macOS 27.0.1, Apple Silicon (arm64), Docker 29.7.2, Python 3.13. On `main`: `pytest -q` **315 passed,
11 skipped**; `RP_LIVE=1` **318 passed, 8 skipped**; `scripts/demo-day1.sh` exit 0 (42 s). PR #29 branch: 317 passed.

Status meanings:
- **DONE**: met, and the evidence is in the repo or was reproduced.
- **PARTIAL**: part is met; the note says which part is missing.
- **MISSING**: no evidence found.

## Summary

| # | Requirement | Before fixes | Now | Still open |
|---|---|---|---|---|
| 1 | AI planner ranks and selects experiments from the dependency map, with a reason each | PARTIAL | **PARTIAL** | The AI only reorders the rule-based ranking (same order as the rules in both logged calls); a human selects, by design |
| 2 | 3+ failure types; errors, slowdowns, recovery time recorded | DONE | **DONE** | Slowdowns are measured, not injected |
| 3 | AI explains, ranks by severity, recommends fixes, gives a resilience score; formula documented | PARTIAL | **PARTIAL** | Severity and score are deterministic (by design); 1 of 7 AI explanations passes validation |
| 4 | Automatic stop on error rate, latency, blast radius, each tested live | PARTIAL | **PARTIAL → DONE when PR #29 merges** | Error rate and blast radius tripped live (on `main`); the latency live trip (`8ad2b8`) and the loader fix that allows it are in PR #29 |
| 5 | Semi-automatic dependency mapping | DONE | **DONE** | — |
| 6 | Kill switch | DONE | **DONE** | — |
| 7 | Human approval before execution | DONE | **DONE** | Now with the approver's typed name on the dashboard |
| 8 | Rule-based fallback when no model is available | DONE | **DONE** | — |
| 9 | Clean run from scratch | PARTIAL | **DONE** (macOS arm64) | Windows not re-tested after these changes |

## Detailed trace

### 1. AI planner ranks and selects experiments from the dependency map, with a reason each: PARTIAL

| Part | Status | Evidence |
|---|---|---|
| Ranking uses the dependency map | DONE (rule-based) | `backend/resiliencepilot/planner.py` (`_dependents(dep_map)`); `config/dependency_map.json`; `test_never_run_shared_store_ranks_first_with_exact_factors`, `test_candidates_come_only_from_the_allowlist` |
| A reason for each entry | DONE | Rule-based: `GET /api/proposals` (`test_proposals_are_the_planner_ranking_with_reasons_and_predictions`), `reports/day2/findings.md`. AI: `reports/day2/ai_advice_qwen2.5-7b-20261010T233726Z.md` |
| The AI ranks | DONE, limited | The AI may only return an exact permutation of the rule-based candidates (`validate_plan`); `test_valid_reorder_is_accepted_but_commands_come_from_the_rule_based_plan`, `test_invalid_plans_fall_back_to_the_rule_based_order`. In both recorded real calls the AI order equalled the rule order |
| Selection | **Human, by design** | The dashboard picker lists the ranked proposals; the user selects one `experiment_id` and approves it with a typed name (PR #25; `test_run_accepts_only_a_catalogue_id_and_a_typed_name`). Nothing selects automatically. The dashboard ranking is rule-based; AI ranking is CLI only (`rp advise`) |

### 2. 3+ failure types with errors, slowdowns and recovery time recorded: DONE

| Part | Evidence |
|---|---|
| 3 failure types | `config/experiments.json`: `telemetry_stall`, `service_crash`, `dependency_failure`; `test_safety.py`, `test_service_crash.py`, `test_dependency_failure.py` |
| Completed live runs | telemetry_stall `20261010T181417Z-c1ab0d`, `…183702Z-21c920` (+ Day 1); service_crash `…181441Z-920732`, `…181503Z-31eafe`, `…181525Z-8fa234`, `…182331Z-b89ae0`, `…183644Z-8941cc`; dependency_failure `…193003Z-e2eacd`, `…203712Z-08b9dd`, `…211735Z-748875` |
| Errors | per poll `http_status`, `error`; `impact.http_error_rate_during_fault` (e.g. `e2eacd`: 1.0) |
| Slowdowns | per poll `latency_ms`; `impact.latency_p95_during_fault_ms` (e.g. `e2eacd`: 3966.72 ms vs baseline 87.87 ms) |
| Recovery time | `recovery.time_to_first_fresh_s`, `recovery.time_to_verified_s` (e.g. `e2eacd`: 24.247 s) |
| Platform next to statistics | `reports/day2/findings.md` "Platform (full-window runs)" column; new run records store `platform` (PR #23). Older runs: "not recorded" unless a committed doc states it (`reports/day2/platforms.json`) |

Note: no failure type **injects** latency (no network-degradation experiment; D-004).

### 3. AI explains findings, ranks by severity, recommends fixes, gives a resilience score; formula documented: PARTIAL

| Part | Status | Evidence |
|---|---|---|
| Explains findings | PARTIAL | AI analyst (`ai.py` `ai_analyse`). In both recorded real calls (`reports/day2/ai_advice_qwen2.5-7b.md`; logged call `reports/day2/ai_calls/20261010T233726Z.json`), **1 of 7** explanations passed validation; 6 were replaced by rule-based text |
| Ranks by severity | DONE, deterministic (not AI) | `findings.py` fixed rule order; `reports/day2/findings.md`; `test_findings_rules_fire_on_their_evidence`. Stated in README "What the AI does and does not do" (PR #24) |
| Recommends fixes | DONE, mostly rule-based | Playbooks per finding in the advice reports; 6 of 7 from the rule-based knowledge base (`ai.py` `KB`) |
| Resilience score | DONE, deterministic (not AI) | `scoring.py`, `rp score`, `reports/day2/scores.json`; `test_scoring.py` |
| Formula documented | DONE | `docs/SCORING.md` |

### 4. Automatic stop on error rate, latency and blast radius, each tested live: PARTIAL

| Guard | Unit-tested | Live |
|---|---|---|
| Error rate | `test_error_rate_trips_and_rolls_back`, `test_error_rate_guard_zero_tolerance_trips_on_first_error` | **Tripped** (on `main`, PR #27). First attempt `20261010T234226Z-e3e15c`: *guard not exercised* (no answer within 8 s). Second attempt `20261010T234332Z-77b84c`: *guard tripped, then fail-closed latch on 30 s recovery* (HTTP 500 at 1.93 s → `guardrail_error_rate`, rollback verified; reset by Vaibhav after checking the stack) |
| Latency | `test_latency_p95_trips_and_rolls_back`, `test_latency_guard_needs_min_samples_then_uses_rolling_p95` | First attempt `20261011T000246Z-46ea3d` (on `main`): *armed, not tripped* (threshold 76.84 ms; polls 44–72 ms). Second and final attempt `20261011T002904Z-8ad2b8`: ***guard tripped with a deliberately tight threshold*** (30 ms cap; first poll 30.89 ms → `guardrail_latency` at 1.88 s; rollback verified; recovery 2.33 s; no latch). **Evidence and the loader fix are in PR #29 (open)**; the margin was 0.89 ms, so it proves the abort path, not a real slowdown |
| Blast radius | `test_blast_radius_breach_trips_rolls_back_and_latches` | **Tripped** in `20261010T182500Z-4aa9f7` (deliberate Grafana restart) |

Demo profiles (merged in PR #27) are tighten-only (enforced in `config.py`; `test_profile_experiment_overrides_only_tighten`,
`test_profile_guardrail_overrides_only_tighten`) and CLI-only (`test_dashboard_refuses_to_run_with_a_config_profile`,
`test_serve_with_a_profile_set_fails_before_binding`; also checked live: `rp serve` with a profile set raises and never
binds port 8765). PR #27's `orchestrator.py` change is only the added `config_profile` field.

### 5. Semi-automatic dependency mapping: DONE

Manual map with evidence (`config/dependency_map.json`), automatic discovery (`rp discover`, `discover.py`), diff
(`reports/day2/discovery.json`), 18 tests in `test_discover.py`. Reproduced live on 2026-10-11: 8 containers, 8
edges; 6 matched, 1 direction differs, 1 discovered only.

### 6. Kill switch: DONE

`scripts/kill-switch.sh` (works without the backend; now executable in git, PR #7), `rp stop`, dashboard Kill.
Tests: `test_kill_switch_file_is_honoured`, `test_emergency_stop_rolls_back_early_and_blocks_new_runs_until_reset`,
`test_kill_from_second_client_rolls_back_and_blocks`. Live: `20261010T084814Z-b17ca6`, `20261010T182400Z-6f6559`, and
the fresh-clone demo on 2026-10-11 (ABORTED by kill switch, recovery verified).

### 7. Human approval before execution: DONE

| Part | Evidence |
|---|---|
| CLI | `approved_by` required, blank rejected (`contracts/experiment_request.schema.json`; `test_malformed_or_invalid_requests_rejected_without_side_effects`) |
| Dashboard | Typed approver name, recorded as given; `dashboard-operator` removed (PR #25). `POST /api/run` accepts exactly `{experiment_id, approved_by}`; target and duration come from the catalogue. Tests: `test_run_accepts_only_a_catalogue_id_and_a_typed_name`, `test_run_records_the_typed_approver_and_what_they_saw`, `test_page_picks_one_catalogue_id_and_a_typed_name` |
| Live | `20261010T233453Z-958562`: started via the picker API with a typed approver, COMPLETED (local record, not committed) |
| What the approver saw | Saved next to the run: `data/proposals/<run_id>.json` (rank, reasons, predicted symptoms, risks) |

### 8. Rule-based fallback when no model is available: DONE

`ai.py` fallbacks; `test_unreachable_or_broken_ollama_falls_back`, `test_offline_is_deterministic`,
`test_cli_advise_offline_never_contacts_ollama`, `test_failed_and_rejected_calls_are_logged_too`;
`reports/day2/ai_advice_offline.md` (provider `rule-based`).

### 9. Clean run from scratch: DONE (macOS Apple Silicon)

Fresh clone of the PR #23 branch into a new folder on 2026-10-11, after deleting the cached `fms-server` and
`fms-consumer` images:

| Step | Result |
|---|---|
| `git clone` + `git -c core.longpaths=true submodule update --init` | OK |
| `./scripts/stack-up.sh` | OK, exit 0 in 16 s; both images pulled as linux/amd64 (D-023) |
| `./scripts/stack-status.sh` | 8/8 running, exit 0; after `stack-down`: exit 1 naming all 8 services |
| venv + `pip install -e ".[test]"` | OK |
| `pytest -q` / `RP_LIVE=1 pytest tests/test_live.py` | 270 passed / 3 passed (test count of that branch at the time) |
| `rp status` | READY |
| `./scripts/demo-day1.sh` | exit 0 in 42 s: stall COMPLETED (stale at 5.18 s with HTTP 200, recovery 3.51 s); kill-switch run ABORTED with recovery verified; next run refused until reset |
| `rp serve` | HTTP 200 |

All four earlier failures are fixed: scripts executable (PR #7), arm64 pull (PR #23), port 55555 conflict (port
55557, PR #7), `stack-status.sh` exit code (PR #23). **Windows was not re-tested** after these changes.

## AI model, calls and logging

| Question | Answer |
|---|---|
| Provider and model | Ollama, loopback only; `qwen2.5:7b`, temperature 0, seed 42, timeout 240 s (`config/ai.json`) |
| Real model calls on record | `reports/day2/ai_advice_qwen2.5-7b.md` (earlier, unlogged) and **`reports/day2/ai_calls/20261010T233726Z.json`** (2026-10-10 23:37 UTC, fully logged) with `reports/day2/ai_advice_qwen2.5-7b-20261010T233726Z.md`. Experiment run records contain no model calls; dashboard runs record `model_calls: []` in their proposal file |
| Where prompts and responses are logged | **Every call** (system prompt, user prompt, raw HTTP response, model, options, error, validation result) → `data/ai_calls/<timestamp>.json` by `rp advise` (PR #25). Tests: `test_every_model_call_is_logged_with_prompts_raw_response_and_validation`, `test_failed_and_rejected_calls_are_logged_too` |

## The DNS claim: resolved (PR #24)

Only facts backed by run records are labelled verified: recovery takes 20–27 s, with a silent (HTTP 200, stale) or
visible (HTTP 500) symptom. The "failed to resolve host name" log observation and the DNS-caching mechanism are
labelled **hypotheses** in `README.md`, `ai.py` `FACTS`, and the rule-based texts, because the log excerpts are not
committed. The AI grounding check rejects unhedged name-resolution claims (`test_unhedged_name_resolution_claim_is_rejected`).

## Personal contact details and secrets: resolved in files (PR #26)

No local home paths and no email addresses remain in tracked files (`git grep`, excluding test fixtures and noreply
addresses). `rp score` and `rp show-baseline` now write repo-relative paths. `docs/TEAM_FORM_INFO.md` has no contact
fields. **Still present:** two personal emails in old git commit metadata. Removing them would need a history rewrite
and force-push, which the repo rules forbid. Upstream demo credentials in the blueprint submodule are public sandbox
defaults (`CLAUDE.md:61`).

## Remaining PARTIAL items

1. **Req 1:** the AI does not select, and its ranking adds nothing over the rules so far. Selection by a human is
   intentional (safety rule 8).
2. **Req 3:** severity and score are deterministic, not AI (intentional), and the 7B model passes 1 of 7
   explanations.
3. **Req 4:** becomes DONE when PR #29 is merged (latency live trip + the tighten-only loader fix that permits it).
4. **Req 9:** Windows clean run not repeated after the fixes.
