# GUARDRAIL-DEMO live evidence

Runs made with the deliberately tight overlay profiles in `config/profiles/` (see its README). Each run record stores
`config_profile`. Host: macOS 27.0.1 on Apple Silicon (arm64); fms-server and fms-consumer run as emulated
linux/amd64 images (D-023). UTC times. Approver: nishtrip14 (runs executed by Claude Code on the lead's instruction).

| Run | Profile | Result | What happened |
|---|---|---|---|
| `20261010T234226Z-e3e15c` | `guardrail-demo-error-rate` (8 s poll timeout) | COMPLETED, `duration_elapsed`; **guard not exercised** | One fault-window poll, no answer within 8 s (`ReadTimeout`); with < 8 s left no further poll started. A missing answer is not an error (observer-loss rule), so the error-rate guard had nothing to judge. Recovery verified 21.1 s after rollback. |
| `20261010T234332Z-77b84c` | `guardrail-demo-error-rate` (2 s poll timeout) | **`guardrail_error_rate` tripped**, rollback verified, then **LATCHED_UNSAFE** | First fault poll at 1.93 s: HTTP 500 in 4.75 ms → error rate 1/1 > 0.0 → abort and roll back (InfluxDB started, same container id). Recovery criteria not met within 30 s (1 advance; first fresh at 30.0 s), so the controller latched. Telemetry was fresh and advancing again within seconds after the latch (`rp observe`). |
| — | `guardrail-demo-latency` | **not run** | Blocked by the latch above; needs the lead's approval to reset first. |
