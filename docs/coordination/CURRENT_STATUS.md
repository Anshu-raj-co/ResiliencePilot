# Current Status

- **Milestone:** Day 2 complete + the lead's fix list (2026-10-11). **Code frozen** after this; docs only.
- **`main`:** PRs #7 and #22–#28 merged. **Open:** PR #29 (latency guardrail demo: tighten-only check against implicit
  defaults + live trip evidence), waiting for the lead's approval. PR #8 open (older).
- **Requirements:** `docs/REQUIREMENTS_TRACE.md`. **Plain-English guide:** `docs/PROJECT_A_TO_Z.md`.

## Verified (2026-10-11, macOS 27.0.1 Apple Silicon, Docker 29.7.2)
- `pytest -q`: 315 passed, 11 skipped. `RP_LIVE=1 pytest -q`: 318 passed, 8 skipped (after PR #27 merge).
- `scripts/demo-day1.sh`: exit 0 (42 s) after the PR #27 merge.
- Fresh clone with fms-server/fms-consumer images pulled from scratch: all README steps pass (PR #23).
- Three experiment types live; guardrails live: blast radius (`4aa9f7`), error rate (`77b84c`, then fail-closed
  latch), latency (`8ad2b8`, deliberately tight 30 ms cap; evidence in PR #29).
- Dashboard picker: typed approver recorded (`958562`); every model call logged (`reports/day2/ai_calls/`).

## Known limitations
- AI only reorders the rule-based ranking; 1 of 7 AI explanations pass validation (7B model).
- No network-degradation experiment (no proxy on the path). Small samples (n = 2–8 per type).
- Windows not re-tested after the 2026-10-11 changes. Watchdog is in-process (kill switch / startup recovery cover a crash).
- Personal emails remain in old git commit metadata (history rewrite forbidden).

## Blockers
- None. Decision pending: merge PR #29.
