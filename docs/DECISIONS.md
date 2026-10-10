# Decisions Log

Format: ID — decision — why — consequences. Newest at bottom.

**D-001 — Target system: Eclipse SDV Fleet Management Blueprint @ upstream `3d514ac72a`, Zenoh transport.**
Why: already present in the repo as a gitlink to that commit; Apache-2.0; prebuilt images are published; Zenoh
variant runs fully locally (Hono variant needs the public Hono sandbox, i.e. an external system — excluded by safety rules).

**D-002 — Override `overlay` networks to `bridge`** (`config/compose/resiliencepilot.override.yaml`).
Why: `overlay` requires Docker Swarm (`docker info`: Swarm inactive). Single-host bridge networks keep the same
topology (two networks, forwarder + router dual-homed). Upstream files are not modified.

**D-003 — Exclude `fleet-analysis-backend` on Day 1.**
Why: no published image (needs local Maven/JDK build; disk has ~18 GB free) and it only reads InfluxDB — it is not on
the producer→consumer telemetry path. Can be added later for Phase 3 dependency experiments.

**D-004 — First fault = `docker pause fms-forwarder`, rollback = `docker unpause`.**
Why: forwarder is the single publisher of VehicleStatus and stamps `createdDateTime`; pausing it stalls the
freshness signal while fms-server keeps answering HTTP 200 — the exact "healthy but stale" story. Freeze/thaw is
reversible and loses no state. There is **no proxy** in the stack, so no network-proxy fault is claimed (override #9).

**D-005 — Normalize CRLF→LF in container-mounted blueprint files inside `scripts/stack-up.sh`.**
Why: Windows `core.autocrlf=true` checks out the blueprint with CRLF; influxdb's init script then fails with
`run-parts: failed to exec … No such file or directory`. Stripping CR is content-neutral for git.

**D-006 — Add `.gitmodules` for the existing gitlink.**
Why: the repo tracked `Fleet Management/fleet-management` as a gitlink (mode 160000) without `.gitmodules`, so fresh
clones got an empty directory. Now `git submodule update --init` restores the pinned upstream revision.

**D-007 — Backend in Python 3.13 (FastAPI + pydantic + httpx), Docker via the `docker` CLI.**
Why: available locally, typed, fast to test; CLI wrapper is one small module that tests replace with a fake.
No Docker socket is ever exposed to an LLM.

**D-008 — Freshness = age of newest `createdDateTime` vs. local clock; progression = strictly increasing `createdDateTime`.**
Why: it is the only data-derived timestamp; there is no sequence id. `receivedDateTime`/`requestServerDateTime` are
`Utc::now()` at request time and are recorded only to demonstrate the misleading-health discrepancy.
Clock note: forwarder and backend run on the same host (Docker Desktop VM clock syncs to Windows), so absolute age is
meaningful; we also record progression, which is clock-independent.
