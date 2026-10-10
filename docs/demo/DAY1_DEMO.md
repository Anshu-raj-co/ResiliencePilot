# Day-1 Demo (≈2 minutes)

One command runs everything live (≈40 s of machine time; the rest is narration):
```bash
./scripts/stack-up.sh          # beforehand, once (≈1 min on first pull)
RP_APPROVER="<presenter name>" ./scripts/demo-day1.sh
```
A real recorded run is in `day1-demo-run.log`. Nothing in the demo is simulated.

| Time | On screen (script step) | Say |
|---|---|---|
| 0:00 | **1/5** 8 containers of the Eclipse SDV Fleet Management blueprint; controller `READY` | "This is the real Eclipse SDV blueprint running locally: CSV vehicle replay → KUKSA databroker → FMS forwarder → Zenoh → consumer → InfluxDB → rFMS API." |
| 0:15 | **2/5** `observe`: `createdDateTime` advances; `receivedDateTime` = request time | "Two timestamps. `receivedDateTime` is just the server's clock at request time, so it always looks fresh. `createdDateTime` is stamped by the vehicle-side forwarder. That one is the truth." |
| 0:30 | **3/5** baseline (16 samples) | "Normal behaviour: HTTP 200 every time, data age about 1 s, a new reading every 1.0 s. Our stale threshold is 5 s, about 4× the worst age we saw." |
| 0:45 | **4/5** `run --duration 12` | "A human approves an allowlisted experiment: freeze the forwarder. Watch: HTTP stays **200** and `receivedDateTime` keeps moving, but `createdDateTime` is frozen. A plain health check says healthy. We flag **HTTP 200 but STALE** after about 5 s." |
| 1:10 | rollback + recovery lines | "Rollback is checked with `docker inspect`. Recovery is *not* 'container running'. We need three new vehicle timestamps, all fresh. Across Day-1 runs: data flowing again 0.5–1.6 s after unpause, recovery verified in 2.6–3.7 s; stale flagged 4.8–5.3 s after injection." |
| 1:25 | **5/5** 25 s run, **KILL SWITCH** fired from a separate process | "Emergency stop from outside the backend. It works even if the backend is dead. Rolled back within a second. Recovery is still verified, and the run is honestly recorded as ABORTED." |
| 1:45 | new run **REFUSED** → reset → `READY` | "After a stop, nothing runs until a human checks the stack and resets. Every run leaves a JSON evidence record." |

## Talking points if asked
- **Safety:** allowlist + compose-project label check, strict schema, one run at a time across processes, total fault time ≤ 30 s
  enforced by a watchdog, a latch on any unverified rollback or recovery, crash recovery. 49 unit tests plus live tests.
  An independent safety review found 1 high and 5 medium issues; all are fixed (`docs/testing/SAFETY_REVIEW.md`).
- **Honesty:** there is no proxy in this stack, so we claim no network fault. The fault is `docker pause` on the one
  publisher on the verified path.
- **Not yet:** AI analyst, dashboard, more experiment types (Phases 3–6 in `docs/ROADMAP.md`).

## If something goes wrong on stage
- `READY` not shown → `cd backend && .venv/Scripts/python -m resiliencepilot status`; read the latch reason; `reset --operator … --note …`.
- Forwarder left paused → `./scripts/kill-switch.sh` (exits non-zero if it could not verify).
- Docker Desktop "unable to start" → quit it fully and relaunch.
