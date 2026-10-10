# Submission form information

Copy-ready answers for the Bosch Hackathon 2026 submission form. Fields marked **[TO FILL]** need information that is
not in the repository; nothing here invents a teammate's identity.

## Project

| Field | Answer |
|---|---|
| Project name | ResiliencePilot SDV |
| Tagline | HTTP 200 is not recovery. Fresh data is. |
| Repository | https://github.com/Anshu-raj-co/ResiliencePilot |
| Demo video | **[TO FILL]** (script: `docs/demo/DAY2_DEMO_SCRIPT.md`) |
| Category / track | **[TO FILL]** |
| Licence | Our code: **[TO FILL]**. Vendored blueprint: Apache-2.0 (Eclipse SDV Fleet Management Blueprint) |

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
databroker, Eclipse Zenoh, InfluxDB, rFMS) · Ollama (`qwen2.5:7b`, local) · vanilla HTML/SVG dashboard · pytest.

## Key results (for the form's "results / impact" field)

- 16 completed live runs across 3 experiment types (8 telemetry_stall, 5 service_crash, 3 dependency_failure),
  plus aborted and latched runs kept as evidence.
- HTTP 200 on 100 % of fault-window polls while telemetry was frozen; stale data detected 4.8–5.3 s after injection.
- After an InfluxDB outage: stale data served as HTTP 200 for ~21–24 s after the database returned (2 runs); recovery
  20.5–24.2 s in the completed runs; stored-data gaps up to 30.7 s.
- Resilience Score medians: telemetry_stall 62.2, service_crash 65.1, dependency_failure 61.6 (Day 2).
- 270 unit tests; 22 logged design decisions; independent safety reviews.

## Team

From the git history, two GitHub accounts have committed to this repository. Names, roles and contact details beyond
that are not recorded here.

| # | Name | GitHub | Role / ownership | Email |
|---|---|---|---|---|
| 1 | Anshu Raj (commit author name) | `Anshu-raj-co` | **[TO FILL]** | **[TO FILL]** |
| 2 | **[TO FILL]** | `nishtrip14` | **[TO FILL]** | **[TO FILL]** |
| 3 | **[TO FILL]** | **[TO FILL]** | **[TO FILL]** | **[TO FILL]** |
| 4 | **[TO FILL]** | **[TO FILL]** | **[TO FILL]** | **[TO FILL]** |
| 5 | **[TO FILL]** | **[TO FILL]** | **[TO FILL]** | **[TO FILL]** |

Suggested ownership areas from the project brief, if useful for the form: engineering lead and integration; SDV
integration and setup; safety controller and fault adapters; telemetry verification, baselines and evidence; AI
analysis, dashboard and reporting.

## AI assistance disclosure

Much of the code and documentation was written with Claude Code (Anthropic), as recorded in commit trailers. All
measurements come from live runs on the team's machines; the run records are committed in `reports/`.

## Attachments checklist

- [ ] Demo video link (3 min)
- [x] README with screenshot and results: `README.md`, `docs/demo/dashboard-day2.png`
- [x] Architecture overview: `docs/ARCHITECTURE_OVERVIEW.md`
- [x] Findings report: `reports/day2/findings.md`
- [x] Demo script: `docs/demo/DAY2_DEMO_SCRIPT.md`
