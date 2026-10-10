# Phase 0 Reconnaissance

Date: 2026-10-10. Labels: **[V]** verified by reading source/config or running a command, **[I]** inference,
**[A]** assumption, **[Q]** open question.

## 1. Environment

| Item | Value | |
|---|---|---|
| OS | Windows 11 Home 10.0.26300 | [V] |
| Host RAM | 15.85 GB | [V] `Win32_ComputerSystem` |
| Docker | Engine 29.6.2, Compose v5.3.1, Docker Desktop 4.85.0 (WSL2 backend) | [V] |
| **Docker VM memory** | **8,248,713,216 bytes ≈ 7.68 GiB**, 16 CPUs | [V] `docker info` |
| Swarm | `inactive` | [V] → blueprint `overlay` networks cannot be created as-is |
| Disk | C: 252 GB, **only ~18 GB free (93 % used)** | [V] `df -h` → avoid building Rust/Java images locally |
| Python / Node / Git / gh | 3.13.5 / 22.18.0 / 2.50.1 / 2.102.0 | [V] |
| GitHub | `gh` logged in as `Anshu-raj-co`, scopes `repo, workflow, read:org, gist` | [V] |
| Claude Code | 2.1.296 | [V] |

**Incident at start:** Docker Desktop was stuck in an error state ("WSL is not installed", logged 07:49 UTC). WSL
3.0.1.0 was in fact installed (`wsl --version` OK); force-quitting and relaunching Docker Desktop fixed it. [V]
If this recurs: quit Docker Desktop fully, relaunch `%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe`.

## 2. Repository state

- Repo `https://github.com/Anshu-raj-co/ResiliencePilot.git`, branch `main`, 3 commits, clean. [V]
- `Fleet Management/fleet-management/` is a vendored copy (no `.git`) of
  **https://github.com/eclipse-sdv-blueprints/fleet-management** (Apache-2.0, `LICENSE` present). [V]
- Revision: 5 sampled files (`fms-blueprint-compose.yaml`, `components/Cargo.toml`, `fms-server/Dockerfile`,
  `fms-server/src/influx_reader.rs`, `grafana/dashboards/FMS-Fleet.json`) have git blob hashes identical to upstream
  `main` at commit **`3d514ac72a`** (2026-10-07). [V] → whole tree is very likely that revision. [I]
- `.github/modernize/java-upgrade/` contains unrelated tool hook scripts; left untouched. [V]

## 3. Services (from `fms-blueprint-compose.yaml` + `fms-blueprint-compose-zenoh.yaml`) [V]

| Service / container | Image | Networks | Host port | Health check | Role |
|---|---|---|---|---|---|
| `influxdb` / `influxDB` | `influxdb:2.7` | backend | 127.0.0.1:8086 | `influx ping` | Time-series store, bucket `demo`, org `sdv` |
| `grafana` | `grafana/grafana:9.5.14` | backend | 127.0.0.1:3000 | none | Dashboard (sdv/sdv) |
| `fms-server` | `ghcr.io/eclipse-sdv-blueprints/fleet-management/fms-server:main` | backend | 127.0.0.1:8081 | none | rFMS HTTP API (axum) reading InfluxDB |
| `fms-consumer` | `…/fms-consumer:main` | backend | – | none | uProtocol listener → writes InfluxDB |
| `databroker` | `quay.io/eclipse-kuksa/kuksa-databroker:0.6.0` | vehicle | 127.0.0.1:55555→55556 | none | KUKSA VSS databroker (gRPC) |
| `csv-provider` | `quay.io/eclipse-kuksa/csv-provider:0.4.5` | vehicle | – | none | Replays `signalsFmsRecording.csv` (4,001 rows) in a loop (`PROVIDER_INFINITE=1`) |
| `fms-forwarder` | `…/fms-forwarder:main` | backend + vehicle | – | none | Reads databroker, publishes `VehicleStatus` every `TIMER_INTERVAL` (500 ms) |
| `fms-zenoh-router` | `eclipse/zenoh:1.1.0` | backend + vehicle | 127.0.0.1:7447 | none | Zenoh router; `restart: unless-stopped` |
| `fleet-analysis-backend` | build-only (Java/Maven) | backend | 127.0.0.1:8082 | none | Analytics over InfluxDB — **not on the telemetry path** |

All ghcr/quay/docker.io images above resolve to linux/amd64 manifests (checked with `docker buildx imagetools inspect`). [V]
`fleet-analysis-backend` has no published image; building it needs Maven + JDK in Docker. Excluded on Day 1
(not on the producer→consumer path, disk is tight). [V/decision D-003]

## 4. Telemetry path (producer → consumer) [V from source]

```
csv-provider ──gRPC──▶ databroker ◀──gRPC get/subscribe── fms-forwarder
                                                           │  every 500 ms (Timer trigger) + on signal triggers
                                                           │  VehicleStatus.created = Timestamp::now()   (kuksa.rs:44)
                                                           ▼  uProtocol notification over Zenoh
                                                    fms-zenoh-router (tcp/7447)
                                                           ▼
                                                    fms-consumer  ──HTTP write──▶ InfluxDB bucket "demo"
                                                    measurements "header"/"snapshot", tags vin, trigger,
                                                    field createdDateTime (epoch ms)  (influx-client/src/writer.rs)
                                                           ▼
                            fms-server GET /rfms/vehiclestatuses?latestOnly=true  (Flux query, last())
                            → createdDateTime  (from data)      receivedDateTime = Utc::now() (influx_reader.rs:372)
                            → requestServerDateTime = Utc::now() (lib.rs)
```

Usable signals:

| Signal | Source | Meaning for us |
|---|---|---|
| `createdDateTime` | Set by **fms-forwarder** wall clock when it assembles a status | **Primary freshness signal**: advances every ~500 ms while forwarder→zenoh→consumer→Influx works. |
| `receivedDateTime`, `requestServerDateTime` | fms-server `Utc::now()` at request time | **Always "fresh"** — useless for freshness; this is exactly the "looks healthy" trap. |
| HTTP status of fms-server | axum | 200 as long as InfluxDB answers, regardless of data age. |
| `triggerType` (`TIMER`, `ENGINE_ON`, …) | forwarder | Lets us count TIMER statuses (expected ~2/s). |
| Value fields (speed, position, …) | csv-provider replay | Change with the CSV; can be static for stretches, so **not** a freshness signal. |
| Sequence number | — | **Unavailable**: VehicleStatus carries no sequence id. Progression = strictly increasing `createdDateTime`. |

Important nuance [V]: because `createdDateTime` is stamped by the forwarder, stopping **csv-provider** would NOT make
timestamps stall (the timer keeps emitting with frozen values). A stall is only visible in `createdDateTime` when the
forwarder → zenoh → consumer → InfluxDB chain breaks.

## 5. Fault-injection feasibility

- **No proxy exists** in the Compose stack (no toxiproxy/envoy). The Zenoh router is a message router, not an
  injectable proxy. Per override #9, network-proxy faults are **not** claimed. [V]
- **Chosen Day-1 fault: `docker pause fms-forwarder`** (cgroup freezer), rollback `docker unpause`. [decision D-004]
  - On the verified path: forwarder is the only publisher of VehicleStatus. [V]
  - Expected effect: fms-server keeps returning 200 with fresh `receivedDateTime`, but `createdDateTime` stops
    advancing. [I — to be measured in Phase 2]
  - Reversible, no data loss, local only.
  - Risk: Zenoh session lease may expire during a long pause, forcing a reconnect on unpause. [Q] Recovery verifier
    must observe this, not assume.
- Alternative later (Phase 3): `docker pause fms-consumer`, `docker stop` + `start` of forwarder/consumer, pausing the
  zenoh router.

## 6. Required local adaptations (no upstream files modified)

1. Networks `fms-backend`/`fms-vehicle` declare `driver: overlay` → requires Swarm. Override to `bridge` in
   `config/compose/resiliencepilot.override.yaml`. [V need; fix to be verified on first `up`]
2. Compose project name pinned to `resiliencepilot-sdv` so the safety controller can verify container labels.
3. Start only the 8 telemetry-path services (+grafana); skip `fleet-analysis-backend`.

## 7. Open questions

- [Q] Zenoh lease/reconnect behaviour after a 10–30 s pause of the forwarder.
- [Q] Real observed cadence/jitter of `createdDateTime` (expected ~500 ms) — measured in baseline.
- [Q] Memory footprint of the running stack on the 7.68 GiB VM.
