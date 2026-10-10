#!/usr/bin/env bash
# Shared compose invocation for the ResiliencePilot sandbox. Source this file.
set -euo pipefail
RP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BLUEPRINT_DIR="$RP_ROOT/Fleet Management/fleet-management"
RP_PROJECT="resiliencepilot-sdv"
# Telemetry-path services only; fleet-analysis-backend is excluded (build-only, not on the path; see DECISIONS D-003).
RP_SERVICES=(influxdb grafana fms-server fms-consumer databroker csv-provider fms-forwarder fms-zenoh-router)
rp_compose() {
  docker compose -p "$RP_PROJECT" \
    --project-directory "$BLUEPRINT_DIR" \
    -f "$BLUEPRINT_DIR/fms-blueprint-compose.yaml" \
    -f "$BLUEPRINT_DIR/fms-blueprint-compose-zenoh.yaml" \
    -f "$RP_ROOT/config/compose/resiliencepilot.override.yaml" \
    "$@"
}
