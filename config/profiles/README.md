# Config profiles (GUARDRAIL-DEMO only)

Profiles are overlays on `config/safety.json` and `config/experiments.json`, selected with `RP_CONFIG_PROFILE=<name>`.
They exist to show the automatic guardrails stopping a run **live**, using deliberately tight thresholds. They are not
for normal use. The allowlist is `CONFIG_PROFILES` in `backend/resiliencepilot/config.py`; any other name is refused.

A profile can only change `guardrails` (safety.json) and these per-experiment fields: `description`,
`expected_symptoms`, `risks`, `expected_statuses`, `max_error_rate`, `fault_observer_timeout_s`,
`fault_latency_p95_max_ms`. Every override is **tighten-only**: guardrail numbers and response limits may only go down versus the default
config, and declared expected statuses may only be removed (enforced in the loader; tested). It cannot change targets, mechanisms, durations, blast radius, the compose project or the
data dir, and it cannot add experiments. Every value still passes the normal bounds checks. The run lock is shared.
Each run record stores `config_profile`. The dashboard refuses to start while a profile is set (`rp serve` exits
with an error), so profiles are CLI-only and cannot be selected from the picker.

| Profile | Change | Expected result |
|---|---|---|
| `guardrail-demo-error-rate` | `dependency_failure` without the expected HTTP 500 | first 500 → `guardrail_error_rate`, rollback |
| `guardrail-demo-error-rate` (cont.) | poll timeout 2 s, latency cap 1.9 s (were 8 s / 7.5 s) so several polls fit in the window | |
| `guardrail-demo-latency` | guardrails only: latency threshold = max(1.0 × baseline p95, 1 ms) (was max(5 ×, 250 ms)), judged from 1 sample; use with `telemetry_stall` | first poll slower than the baseline p95 → `guardrail_latency`, rollback. Trips on normal jitter by design: it proves the abort path, not a real slowdown |

```bash
cd backend
RP_CONFIG_PROFILE=guardrail-demo-error-rate .venv/bin/rp run --type dependency_failure --target influxDB \
  --duration 12 --approved-by "<you>" --hypothesis "GUARDRAIL-DEMO error rate"
```
Evidence: `reports/guardrail-demo/`.
