#!/usr/bin/env bash
# Emergency stop that does NOT depend on the ResiliencePilot backend being alive.
#  1. writes the EMERGENCY_STOP file -> a running experiment rolls back at its next poll and new runs are refused
#  2. directly unpauses every allowlisted target that is paused in the sandbox project
# Afterwards: python -m resiliencepilot reset --operator NAME --note "..."
set -euo pipefail
RP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$RP_ROOT/backend/data"
mkdir -p "$DATA_DIR"
printf '{"at": "%s", "source": "kill-switch.sh"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$DATA_DIR/EMERGENCY_STOP"
echo "EMERGENCY_STOP written to $DATA_DIR"

# Allowlist read from config/experiments.json (no other container is ever touched)
TARGETS=$(python -c "import json,sys; d=json.load(open(sys.argv[1])); print(' '.join(sorted({t for s in d.values() for t in s['allowed_targets']})))" "$RP_ROOT/config/experiments.json")
for t in $TARGETS; do
  project=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$t" 2>/dev/null || true)
  if [ "$project" != "resiliencepilot-sdv" ]; then
    echo "skip $t (not in sandbox project: '$project')"
    continue
  fi
  if [ "$(docker inspect -f '{{.State.Paused}}' "$t")" = "true" ]; then
    docker unpause "$t" >/dev/null && echo "unpaused $t"
  else
    echo "$t not paused"
  fi
  echo "$t: $(docker inspect -f 'running={{.State.Running}} paused={{.State.Paused}}' "$t")"
done
