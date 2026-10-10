#!/usr/bin/env bash
# Show container state and the latest telemetry as seen through the public rFMS API.
source "$(dirname "$0")/_compose.sh"
rp_compose ps --format 'table {{.Name}}\t{{.State}}\t{{.Status}}'
echo
curl -s -w '\nHTTP %{http_code}\n' 'http://127.0.0.1:8081/rfms/vehiclestatuses?latestOnly=true' | head -c 600
echo
