#!/usr/bin/env bash
# Emergency stop that does NOT depend on the ResiliencePilot backend being alive.
#  1. writes the EMERGENCY_STOP file -> a running experiment rolls back at its next poll and new runs are refused
#  2. directly unpauses every allowlisted target that is paused in the sandbox project
# Exit code 0 only if every allowlisted target is verified unpaused afterwards.
# Afterwards: python -m resiliencepilot reset --operator NAME --note "..."
set -uo pipefail   # no -e: one failing docker call must not skip the remaining rollback steps
RP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
read -r PROJECT DATA_DIR TARGETS < <(python - "$RP_ROOT" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
cfg = json.loads((root / "config" / "safety.json").read_text())
cat = json.loads((root / "config" / "experiments.json").read_text())
data = pathlib.Path(cfg["data_dir"]); data = data if data.is_absolute() else root / data
targets = ",".join(sorted({t for s in cat.values() for t in s["allowed_targets"]}))
print(cfg["compose_project"], data.as_posix(), targets)
PY
)
if [ -z "${PROJECT:-}" ] || [ -z "${TARGETS:-}" ]; then
  echo "could not read config; falling back to defaults" >&2
  PROJECT="resiliencepilot-sdv"; DATA_DIR="$RP_ROOT/backend/data"; TARGETS="fms-forwarder"
fi

mkdir -p "$DATA_DIR"
printf '{"at": "%s", "source": "kill-switch.sh"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$DATA_DIR/EMERGENCY_STOP" \
  && echo "EMERGENCY_STOP written to $DATA_DIR" || echo "WARNING: could not write EMERGENCY_STOP file" >&2

status=0
for t in ${TARGETS//,/ }; do
  project=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$t" 2>/dev/null || true)
  if [ "$project" != "$PROJECT" ]; then
    echo "skip $t (not in sandbox project: '$project')"
    continue
  fi
  for attempt in 1 2 3; do
    paused=$(docker inspect -f '{{.State.Paused}}' "$t" 2>/dev/null || echo "unknown")
    [ "$paused" = "false" ] && break
    if [ "$paused" = "true" ]; then
      docker unpause "$t" >/dev/null 2>&1 && echo "unpaused $t" || echo "unpause $t failed (attempt $attempt)" >&2
    fi
    sleep 0.5
  done
  final=$(docker inspect -f 'running={{.State.Running}} paused={{.State.Paused}}' "$t" 2>/dev/null || echo "inspect failed")
  echo "$t: $final"
  [ "$final" = "running=true paused=false" ] || status=1
done
[ $status -eq 0 ] || echo "KILL SWITCH INCOMPLETE: a target is not verified running+unpaused. Check Docker manually." >&2
exit $status
