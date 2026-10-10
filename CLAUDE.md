# ResiliencePilot SDV — Engineering Instructions

Read this first, then `docs/coordination/HANDOFF.md` and `docs/coordination/CURRENT_STATUS.md`.
The original product brief (with its priority OVERRIDES) is in `docs/BRIEF.md`.

## Product in one paragraph

ResiliencePilot is a local-first resilience workbench for the **Eclipse SDV Fleet Management Blueprint**
(vendored at `Fleet Management/fleet-management/`, Apache-2.0). It proves that the rFMS API can return
HTTP 200 while vehicle telemetry is stale, detects that discrepancy from real observations, runs
bounded/reversible fault experiments behind a deterministic safety controller, and independently
verifies recovery from advancing telemetry — never from HTTP 200 or "container running" alone.

## Repository layout

| Path | Purpose |
|---|---|
| `Fleet Management/fleet-management/` | Vendored upstream blueprint. **Do not edit**; adapt via `config/compose/` overrides. |
| `config/compose/` | Our Compose override(s) for running the blueprint locally. |
| `config/` | Safety limits and target allowlist. |
| `contracts/` | JSON Schemas shared by backend/frontend/AI (source of truth for interfaces). |
| `backend/resiliencepilot/` | Python package: SDV adapter, telemetry verifier, baseline, safety controller, fault adapters, orchestrator, evidence store, API/CLI. |
| `backend/tests/` | pytest unit + integration tests. |
| `scripts/` | Start/stop stack, run baseline, run demo. |
| `experiments/` | Experiment definitions (allowlisted catalogue). |
| `reports/` | Committed sample reports from real runs only; generated output goes to `reports/generated/` (gitignored). |
| `docs/` | Recon, architecture, decisions, roadmap, testing, demo, coordination. |

## Safety rules (non-negotiable)

1. Faults only against containers of the local Compose project `resiliencepilot-sdv`, by **exact allowlisted
   container name** AND matching `com.docker.compose.project` label. Never anything else.
2. Only predefined experiment types (`contracts/experiment_request.schema.json`). Strict validation; unknown fields rejected.
3. One active experiment at a time, across processes (`backend/data/run.lock` with owner PID). Total fault time
   ≤ 30 s including watchdog grace (config may set lower, never higher).
4. Watchdog thread enforces deadline independent of API/UI/AI. Emergency stop always available (API + CLI + `scripts/kill-switch`).
5. Rollback on: completion, cancel, timeout, safety-threshold violation, observer loss, any exception (`finally`).
6. Recovery = rollback verified by `docker inspect` AND `createdDateTime` advancing 3 successive times past both the
   last frozen value and the server time of the last fault-window poll, each fresh (≤ 5 s) with HTTP 200. HTTP 200 alone is never recovery.
7. Rollback or recovery not verified → **LATCHED_UNSAFE**. No new experiments until an explicit, logged reset after a human checks the stack.
8. The LLM (Phase 4+) never gets shell, Docker socket, credentials, or the ability to inject/rollback/decide recovery. Advisory only.
9. Never claim injection, recovery, test results, or measurements that were not actually observed.

## Engineering conventions

- Python 3.13, typed (dataclasses/pydantic), small modules. Docker is driven via the `docker` CLI through a single
  wrapper (`backend/resiliencepilot/docker_cli.py`) so it can be faked in tests.
- Every new behaviour needs a test. Safety-controller changes need negative-path tests.
- Run tests: `cd backend && .venv/Scripts/python -m pytest -q` (Windows) or `.venv/bin/python -m pytest -q`.
- Integration tests that need the live stack are marked `@pytest.mark.live` and skipped unless `RP_LIVE=1`.

## Git workflow

- `main` stays stable. Work on `wip/<topic>` or `feat/<topic>` branches; open PRs to `main`.
- Checkpoint commit + push every 20–30 min; update `docs/coordination/HANDOFF.md` each time.
- No force-push, no secrets. The blueprint's demo InfluxDB credentials are upstream sandbox defaults, local-only.

## Continuing work in a new session

1. `git fetch && git status`; check out the branch named in `HANDOFF.md`.
2. Read `HANDOFF.md` → "Next concrete task". Read `CURRENT_STATUS.md` for what is verified.
3. Start the stack with `scripts/stack-up.sh`, check `scripts/stack-status.sh`.
4. Run the test suite before changing anything.
