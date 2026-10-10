# Config profiles (GUARDRAIL-DEMO only)

Profiles are overlays on `config/safety.json` and `config/experiments.json`, selected with `RP_CONFIG_PROFILE=<name>`.
They exist to show the automatic guardrails stopping a run **live**, using deliberately tight thresholds. They are not
for normal use. The allowlist is `CONFIG_PROFILES` in `backend/resiliencepilot/config.py`; any other name is refused.

A profile can only change `guardrails` (safety.json) and these per-experiment fields: `description`,
`expected_symptoms`, `risks`, `expected_statuses`, `max_error_rate`, `fault_observer_timeout_s`,
`fault_latency_p95_max_ms`. It cannot change targets, mechanisms, durations, blast radius, the compose project or the
data dir, and it cannot add experiments. Every value still passes the normal bounds checks. The run lock is shared.
Each run record stores `config_profile`.

| Profile | Change | Expected result |
|---|---|---|
| `guardrail-demo-error-rate` | `dependency_failure` without the expected HTTP 500 | first 500 → `guardrail_error_rate`, rollback |
| `guardrail-demo-latency` | `dependency_failure` latency cap 1000 ms (was 7500), latency judged from 1 sample | first slow poll → `guardrail_latency`, rollback |

```bash
cd backend
RP_CONFIG_PROFILE=guardrail-demo-error-rate .venv/bin/rp run --type dependency_failure --target influxDB \
  --duration 12 --approved-by "<you>" --hypothesis "GUARDRAIL-DEMO error rate"
```
Evidence: `reports/guardrail-demo/`.
