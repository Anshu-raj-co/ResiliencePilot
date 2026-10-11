# Demo video script (2 minutes)

A screen recording of the real local stack plus the data-quality gate. It is a tighter cut of
`docs/demo/DAY2_DEMO_SCRIPT.md` (3 min, which has the full set-up and recovery notes) with a new 20-second
data-quality section. One live experiment (`telemetry_stall`). Recorded runs are labelled on screen. Nothing is simulated.
The data-quality feed is **synthetic** and the video says so.

## Before recording (not on camera)

```bash
./scripts/stack-up.sh && ./scripts/stack-status.sh
cd backend
.venv/bin/rp status                       # READY
.venv/bin/rp serve &                      # dashboard http://127.0.0.1:8765/
# data-quality gate: fresh scratch store, two feeds ready
dq() { .venv/bin/python -m resiliencepilot.dq --db ../reports/generated/dq-demo.db "$@"; }   # bash and zsh
rm -f ../reports/generated/dq-demo.db
dq generate --count 100 --error-count 2  --out ../reports/generated/feed-2.json
dq generate --count 100 --error-count 10 --seed 7 --out ../reports/generated/feed-10.json
```
Terminal in `backend/` with a large font. Keep `docs/scene/index.html` open in a second browser tab.

## Script

| Time | Screen | Voice-over |
|---|---|---|
| **0:00–0:15** · Hook | Dashboard: gauge green, **HTTP 200**, FRESH | "When a fleet API says 200, is the truck data actually fresh? We tested the Eclipse SDV Fleet Management Blueprint. Not always." |
| **0:15–0:50** · Live fault | Pick **telemetry_stall**, type your name, **Approve & start**. The HTTP chip stays **200**, the gauge turns red, banner "STALE while HTTP 200" | "A human approves one allowlisted fault: freeze the vehicle-side forwarder for 12 seconds. Every poll still returns 200, but the vehicle's own timestamp has stopped. We flag stale data about five seconds in." |
| **0:50–1:05** · Verified recovery | Timeline *Rolling back → Recovered*; Last-run card | "Rollback is automatic. Recovery counts only after three fresh, advancing vehicle timestamps. HTTP 200 alone never counts. If we can't verify it, the system latches for a human." |
| **1:05–1:25** · Worst case (recorded) | Toggle **Committed Day-2 evidence** → Findings F1; label "recorded run" | "Our strongest finding: after the database came back, the API served frozen data as HTTP 200 for over 20 seconds while every container looked healthy." |
| **1:25–1:45** · **Data quality (20 s)** | Terminal: `dq ingest --file ../reports/generated/feed-2.json` → *COMMITTED_WITH_QUARANTINE, clean 98, quarantine 2*; `dq ingest --file ../reports/generated/feed-10.json` → *ROLLED_BACK … OPERATOR REVIEW*; `dq show-quarantine` (2 records with `AMBIGUOUS_DATE_FORMAT` and `TIMESTAMP_IN_FUTURE`); `dq report --out ../reports/generated/dq-report.html` and open it: donut and error-code table | "Telemetry also arrives dirty. On a synthetic multi-region logistics feed we normalize every timestamp to one UTC format. An ambiguous date like 03/04 is never guessed, and future timestamps are rejected. Two bad records in a hundred: we commit 98 and quarantine 2 with reasons. Ten in a hundred: the whole batch rolls back for a human." |
| **1:45–1:55** · Safety + AI | `rp status` (READY); README *Safety model* | "Allowlisted containers only, at most 30 seconds, one experiment at a time, watchdog and kill switch. The local AI only advises, and every answer is checked against the evidence." |
| **1:55–2:00** · Close | README results table | "ResiliencePilot: HTTP 200 is not recovery. Fresh data is." |

## Numbers quoted (all observed)

| Claim | Source |
|---|---|
| Stale flagged ~5 s after injection; three fresh timestamps to recover | `reports/day1/`, `reports/day2/` telemetry_stall runs (4.8–5.3 s) |
| Stale HTTP 200 for over 20 s after the database returned | `reports/day2/README.md`, runs `179795` and `01d396` |
| 98 committed / 2 quarantined; 10 of 100 → rollback | your own on-camera run (the generator places exactly N invalid records); also `docs/dq/report.html` |
| Test counts, if mentioned: 396 passed, 11 skipped | `pytest -q` on `feat/data-quality` (317 existing + 79 data-quality) |

The live numbers will differ slightly from these ranges. Read them from the screen.
