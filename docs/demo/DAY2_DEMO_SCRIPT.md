# Day-2 demo video script (3 minutes)

Screen recording of a real local stack. One live experiment (the dashboard's fixed 12 s stall); everything else is
shown from recorded runs, and the script says so on screen. Nothing is simulated.

## Before recording (not on camera)

```bash
./scripts/stack-up.sh && ./scripts/stack-status.sh          # 8 containers, HTTP 200 with vehicle data
cd backend
.venv/bin/rp status                                          # must be READY, emergency_stop_engaged false
.venv/bin/rp serve &                                         # dashboard on http://127.0.0.1:8765/
.venv/bin/rp advise --dir ../reports/day2 --offline --out ../reports/generated/advice.md   # instant, for scene 6
```
Browser: dashboard at 100 % zoom, "Local runs" selected. Terminal: large font, in `backend/`.
Have `reports/day2/findings.md` open in a second tab. Take 1 s pauses between scenes for editing.

## Script

| Time | Screen | Voice-over |
|---|---|---|
| **0:00–0:20** · Hook | Dashboard top row: gauge green, **HTTP 200**, FRESH | "Connected trucks report through an rFMS API. When that API says 200, is the fleet data actually fresh? We tested the Eclipse SDV Fleet Management Blueprint, and the answer is: not always." |
| **0:20–0:35** · The trick | Zoom on *Latest observation*: `createdDateTime` vs `receivedDateTime` | "`receivedDateTime` is just the server's clock, so it always looks fresh. `createdDateTime` is stamped on the vehicle side. ResiliencePilot judges health by that one, with a 5-second staleness threshold." |
| **0:35–1:15** · Live fault | Click **Start** → confirm dialog (approved template) → timeline goes *Fault active → Stale detected*; gauge turns red; HTTP chip stays **200**; banner "STALE while HTTP 200" | "A human approves one allowlisted experiment: freeze the vehicle-side forwarder for 12 seconds. Watch the HTTP status: it never changes. Every poll is 200. A normal health check would say all good. We flag stale data about five seconds in." |
| **1:15–1:35** · Verified recovery | Timeline *Rolling back → Recovered*; Last-run card: rollback verified, recovery verified ~3 s | "Rollback is automatic. We don't trust 'container running' or HTTP 200 as recovery: only three new vehicle timestamps in a row count. If that can't be verified, the system latches and refuses new experiments until a person checks." |
| **1:35–1:55** · Score | Scroll to *Resilience Score* card (now this run): component bars, silent polls, penalty | "Each run gets a Resilience Score from what was measured: detection time, recovery time, data loss, and blast radius, then cut by up to half for the polls where the API answered 200 with stale data." |
| **1:55–2:20** · The worst case (recorded) | Toggle **Committed Day-2 evidence**; *Findings* card: F1/F2 *Stale data served as HTTP 200 after the fault was removed*; on-screen label "recorded run" | "Our strongest finding came from stopping the database. The API showed errors, which is good, but after the database came back, the consumer kept failing to resolve its host name for over 20 seconds, while the API answered 200 with frozen data. Container checks: healthy. Our recovery rule: not recovered. That run latched." |
| **2:20–2:40** · Planner + AI | *Next experiments* card; then terminal: `head -30 ../reports/day2/ai_advice_qwen2.5-7b.md` showing an "AI answer rejected" line | "A rule-based planner ranks the next experiment, most informative and riskiest first; it can propose, never run. A local LLM can reorder and explain, but every root cause is checked against the evidence. With a 7B model, six of seven answers were rejected and replaced by the rule-based ones. We show that, because it's what happened." |
| **2:40–2:55** · Safety | Terminal: `.venv/bin/rp status` (READY); show `scripts/kill-switch.sh` name; README *Safety model* | "Faults only hit allowlisted containers in this local sandbox, at most 30 seconds, one at a time, with a watchdog, a kill switch that works even if our backend is dead, and 270 tests." |
| **2:55–3:00** · Close | README results table | "ResiliencePilot: HTTP 200 is not recovery. Fresh data is." |

## Numbers quoted (all from recorded runs)

| Claim in the voice-over | Source |
|---|---|
| Stale flagged ~5 s after injection; recovery verified ~3 s after rollback | `reports/day1/`, `reports/day2/` telemetry_stall runs (4.8–5.3 s; 2.6–3.7 s) |
| Stale HTTP 200 for over 20 s after the database returned; one run latched | `20261010T192839Z-179795` (~23.5 s, not recovered in 30 s), `20261010T203817Z-01d396` (~21 s) |
| AI: 6 of 7 answers rejected | `reports/day2/ai_advice_qwen2.5-7b.md`, `docs/AI_LAYER.md` |
| 270 tests | `pytest -q` on main after PR #20 |

The live run's own numbers (detection time, recovery time, score) will differ slightly from these ranges; read them
from the screen, don't quote the table.

## If something goes wrong while recording

- Start disabled → banner shows the reason; `rp status`, check the stack, `rp reset --operator NAME --note "..."`.
- Forwarder left paused → `./scripts/kill-switch.sh`.
- Dashboard shows "backend unreachable" → restart `rp serve`.
