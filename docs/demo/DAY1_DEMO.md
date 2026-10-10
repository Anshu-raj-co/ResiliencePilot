# Day-1 Demo (≈2 minutes)

One command runs everything live (≈40 s of machine time; the rest is narration):
```bash
./scripts/stack-up.sh          # beforehand, once (≈1 min on first pull)
RP_APPROVER="<presenter name>" ./scripts/demo-day1.sh
```
A real recorded run is in `day1-demo-run.log`. Nothing in the demo is simulated.

| Time | On screen (script step) | Say |
|---|---|---|
| 0:00 | **1/6** 8 containers + `deps`: telemetry path and edges with confidence labels | "This is the real Eclipse SDV blueprint. Every edge comes from a file:line in its source, is checked live on the Docker network, and the telemetry path was confirmed by experiment." |
| 0:15 | **2/6** `observe`: `created` advances; `received` = request time; green age bar | "`received` is just the server's clock, so it always looks fresh. `created` is stamped by the vehicle-side forwarder. That one is the truth." |
| 0:25 | **3/6** reference baseline (60 samples) + a fresh 16-sample one, both with load conditions | "Normal: HTTP 200 every time, data age about 1 s, a new reading every 1.0 s. One vehicle, CSV replay, about 220 MiB for the whole stack. Our stale threshold is 5 s, about 4× the worst age we saw." |
| 0:40 | **4/6** `run --duration 12`: the age bar grows past the `|` threshold, turns red, **HTTP 200 but STALE** | "A human approves an allowlisted fault: freeze the forwarder. HTTP stays 200 on every poll. A health check would say healthy. We flag stale at about 5 s." |
| 1:05 | Impact chart + timeline printed after the run | "Here's the whole experiment as a picture: the age climbs, HTTP stays 2 the whole time, then rollback. Recovery is only declared after three new vehicle timestamps, about 3–4 s after rollback." |
| 1:25 | **5/6** 25 s run, **KILL SWITCH** from a separate process | "Emergency stop from outside the backend; it works even if the backend is dead. In our timed run, rollback was verified 0.6 s after the switch, then recovery was verified, recorded honestly as ABORTED." |
| 1:45 | **6/6** new run **REFUSED** → reset → `READY` | "After a stop nothing runs until a human checks and resets. Full numbers are in the judge report." |

Optional second screen: Grafana `http://127.0.0.1:3000/d/OwEBnb1Vd/fms-fleet` (sdv/sdv), the blueprint's own dashboard
(the endpoint was verified reachable; how its panels look during a stall has not been checked yet). Run the demo
slower with `RP_DEMO_PAUSE=3`.

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
