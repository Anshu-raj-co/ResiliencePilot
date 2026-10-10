#!/usr/bin/env bash
# ResiliencePilot Day-1 demo (~2 min). Prereq: ./scripts/stack-up.sh already run, backend venv installed.
# Every number shown is measured live; nothing is simulated.
set -uo pipefail
RP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$RP_ROOT/backend/.venv/Scripts/python"; [ -x "$PY" ] || PY="$RP_ROOT/backend/.venv/bin/python"
export PYTHONIOENCODING=utf-8
rp() { (cd "$RP_ROOT/backend" && "$PY" -m resiliencepilot "$@"); }
APPROVER="${RP_APPROVER:-demo-operator}"
say() { printf '\n\033[1;36m=== %s ===\033[0m\n' "$*"; }

say "1/5  The real Eclipse SDV Fleet Management stack is running"
docker ps --filter "label=com.docker.compose.project=resiliencepilot-sdv" --format '  {{.Names}}\t{{.Status}}' | sort
rp status | head -2

say "2/5  Healthy now: createdDateTime (vehicle) advances; receivedDateTime is just the server's clock"
rp observe -n 4

say "3/5  Baseline: 16 samples over 8 s"
rp baseline -n 16 -i 0.5 | grep -E '"(samples|http_ok_ratio|median|max|FRESH|STALE)"|saved'

say "4/5  Approved experiment: freeze fms-forwarder for 12 s. Watch HTTP stay 200 while data goes stale"
rp run --duration 12 --approved-by "$APPROVER" \
  --hypothesis "Pausing fms-forwarder stalls createdDateTime while the rFMS API keeps returning HTTP 200" --yes \
  | grep -v -E '^  (type|target|duration|hypothesis|expected|risks|stop anytime)'

say "5/5  Kill switch: 25 s experiment, emergency stop fired from a separate process after 6 s"
( sleep 6; printf '\n\033[1;31m>>> KILL SWITCH\033[0m\n'; bash "$RP_ROOT/scripts/kill-switch.sh" ) &
rp run --duration 25 --approved-by "$APPROVER" --hypothesis "Kill switch rolls back well before 25 s" --yes \
  | grep -E '^»|^Result|^  (rollback|during)'
wait
echo; echo "New runs are refused until a human resets after checking the stack:"
rp run --duration 5 --approved-by "$APPROVER" --yes | tail -1
rp reset --operator "$APPROVER" --note "demo: stack checked after kill switch" >/dev/null && rp status | head -2
say "Done. Evidence: backend/data/runs/  (rp runs / rp show <id>)"
