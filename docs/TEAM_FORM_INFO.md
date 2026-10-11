# Submission form information

Copy-ready answers for the Bosch Hackathon 2026 submission form. Fields marked **[TO FILL]** still need information.
The team roster and metadata below were provided by the team.

## Project

| Field | Answer |
|---|---|
| Project name | ResiliencePilot SDV |
| Tagline | HTTP 200 is not recovery. Fresh data is. |
| Repository | https://github.com/Anshu-raj-co/ResiliencePilot |
| Demo video | **[TO FILL]** (script: `docs/demo/DAY2_DEMO_SCRIPT.md`) |
| Category / track | AI-Driven Chaos Engineering for Cloud-Native Resilience |
| Licence | Our code: MIT. Vendored blueprint: Apache-2.0 (Eclipse SDV Fleet Management Blueprint) |

## Short description (≈50 words)

ResiliencePilot is a local resilience workbench for the Eclipse SDV Fleet Management Blueprint. It proves the rFMS
API can return HTTP 200 while vehicle telemetry is stale, runs bounded fault experiments behind a deterministic
safety controller, and verifies recovery only from advancing vehicle timestamps, never from HTTP 200 alone.

## Long description (≈200 words)

Connected-vehicle back ends are usually monitored by HTTP status and container health. We show that this is not
enough. In the Eclipse SDV Fleet Management Blueprint, the rFMS API keeps answering HTTP 200 with a fresh-looking
`receivedDateTime` while the vehicle's `createdDateTime` is frozen.

ResiliencePilot observes the API like a client, injects three kinds of allowlisted, reversible faults (freeze the
vehicle publisher, crash it, stop the time-series database), and measures detection time, recovery time, stored-data
loss and blast radius. Recovery is only declared after three successive fresh vehicle timestamps; otherwise the
system latches until a human resets it. A watchdog caps every fault at 30 seconds and a kill switch works even when
the backend is dead.

Across 16 completed live runs, the API answered HTTP 200 with stale data in every publisher fault, and after a
database outage the stack kept serving stale data as HTTP 200 for over 20 seconds while every container looked
healthy. Each run gets a Resilience Score; a rule-based planner ranks the next experiment, and a local LLM advisor
explains findings, with every AI root cause checked against the recorded evidence.

## Technologies

Python 3.13 · FastAPI · httpx · pydantic · Docker / Docker Compose · Eclipse SDV Fleet Management Blueprint (KUKSA
databroker, Eclipse Zenoh, InfluxDB, rFMS) · Ollama (`qwen2.5:7b`, local) · vanilla HTML/SVG dashboard · pytest ·
Three.js r160 (MIT; vendored as `docs/scene/three.r160.min.js` with its licence, for the optional offline 3D
visualization of recorded runs in `docs/scene/`).

## Key results (for the form's "results / impact" field)

- 16 completed live runs across 3 experiment types (8 telemetry_stall, 5 service_crash, 3 dependency_failure),
  plus aborted and latched runs kept as evidence.
- HTTP 200 on 100 % of fault-window polls while telemetry was frozen; stale data detected 4.8–5.3 s after injection.
- After an InfluxDB outage: stale data served as HTTP 200 for ~21–24 s after the database returned (2 runs); recovery
  20.5–24.2 s in the completed runs; stored-data gaps up to 30.7 s.
- Resilience Score medians: telemetry_stall 62.2, service_crash 65.1, dependency_failure 61.6 (Day 2).
- 317 unit tests (320 with live tests); 23 logged design decisions; independent safety reviews.

## Team

Only names and public GitHub handles are recorded here. **Do not commit personal contact details (email, phone) to the
repository:** enter them only in the submission form itself.

| # | Name | GitHub | Role / ownership |
|---|---|---|---|
| 1 | Vaibhav Raghav | `Vaibhavraghav108` | Lead Architect & Core Engineering (Research lead, safety controller, hardware watchdog, integration) |
| 2 | Anshu Raj | `Anshu-raj-co` | SDV Integration & Chaos Adapters (Blueprint setup, fault injection adapters, container orchestration) |
| 3 | Nishant Tripathi | `nishtrip14` | Telemetry Verification & Scoring (rFMS observation, freshness tracking, Resilience Score engine) |
| 4 | Jahnavi | `jahnavi` | AI Safety Governance & Validation (Hallucination guardrail, rule-based planner fallback, findings verification) |
| 5 | Samhithaa | `samhithaa` | Digital Twin Visualization & Reporting (Dashboard, evidence records, findings & metrics documentation) |

## AI assistance disclosure

Much of the code and documentation was written with Claude Code (Anthropic), as recorded in commit trailers. All
measurements come from live runs on the team's machines; the run records are committed in `reports/`.

## Attachments checklist

- [ ] Demo video link (3 min)
- [x] README with screenshot and results: `README.md`, `docs/demo/dashboard-day2.png`
- [x] Architecture overview: `docs/ARCHITECTURE_OVERVIEW.md`
- [x] Findings report: `reports/day2/findings.md`
- [x] Demo script: `docs/demo/DAY2_DEMO_SCRIPT.md`
