#!/usr/bin/env bash
# ResiliencePilot Day-1 demo (~2 min). Prereq: ./scripts/stack-up.sh already run, backend venv installed.
# Every number shown is measured live or read from a stored real run; nothing is simulated.
set -uo pipefail
RP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$RP_ROOT/backend/.venv/Scripts/python"; [ -x "$PY" ] || PY="$RP_ROOT/backend/.venv/bin/python"
export PYTHONIOENCODING=utf-8
[ -t 1 ] && export FORCE_COLOR=1        # keep colours on screen; plain text when logging to a file
rp() { (cd "$RP_ROOT/backend" && "$PY" -m resiliencepilot "$@"); }
APPROVER="${RP_APPROVER:-demo-operator}"
PAUSE="${RP_DEMO_PAUSE:-0}"             # seconds to wait between steps when presenting
say() { sleep "$PAUSE"; printf '\n\033[1;36m=== %s ===\033[0m\n' "$*"; }
REF_BASELINE="$RP_ROOT/reports/day1/baseline-20261010T094007Z.json"

say "1/6  The real Eclipse SDV Fleet Management stack, and its verified dependency map"
docker ps --filter "label=com.docker.compose.project=resiliencepilot-sdv" --format '  {{.Names}}\t{{.Status}}' | sort
rp deps

say "2/6  Healthy now: createdDateTime (vehicle) advances; receivedDateTime is just the server's clock"
rp observe -n 4

say "3/6  Baseline: reference (60 samples, committed) and a fresh one taken right now, with load conditions"
rp show-baseline "$REF_BASELINE"
echo
rp baseline -n 16 -i 0.5

say "4/6  Approved experiment: freeze fms-forwarder for 12 s. HTTP stays 200 while the age bar crosses the threshold"
rp run --duration 12 --approved-by "$APPROVER" \
  --hypothesis "Pausing fms-forwarder stalls createdDateTime while the rFMS API keeps returning HTTP 200" --yes

say "5/6  Kill switch: 25 s experiment, emergency stop fired from a separate process after 6 s"
( sleep 6; printf '\n\033[1;31m>>> KILL SWITCH (separate process)\033[0m\n'; bash "$RP_ROOT/scripts/kill-switch.sh" ) &
rp run --duration 25 --approved-by "$APPROVER" --hypothesis "Kill switch rolls back well before 25 s" --yes \
  | grep -v -E '^  \[(precheck|recovery)'
wait

say "6/6  After a stop nothing runs until a human resets"
rp run --duration 5 --approved-by "$APPROVER" --yes | tail -1
rp reset --operator "$APPROVER" --note "demo: stack checked after kill switch" >/dev/null && rp status | head -2
say "Done. Evidence: backend/data/runs/ (rp runs; rp show <id>). Judge report: docs/demo/DAY1_REPORT.md"
