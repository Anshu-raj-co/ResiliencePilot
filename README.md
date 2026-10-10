# Bosch-2026
Bosch Hackathon

## ResiliencePilot SDV

A local resilience workbench for the [Eclipse SDV Fleet Management Blueprint](https://github.com/eclipse-sdv-blueprints/fleet-management)
(pinned as a submodule at `3d514ac72a`). It shows that the blueprint's rFMS API can keep answering **HTTP 200 with a
fresh-looking `receivedDateTime` while the vehicle telemetry (`createdDateTime`) is frozen**. It detects that, injects
the fault on purpose under a deterministic safety controller, rolls it back, and verifies recovery from the data itself.

Status: **Day 1, Phases 0–2.** See `docs/coordination/CURRENT_STATUS.md`. Not production software and not
safety-certified. Faults only ever target the local sandbox.

### Quick start (Windows + Docker Desktop, Git Bash)
```bash
git submodule update --init                      # fresh clone only
./scripts/stack-up.sh                            # pulls prebuilt images, starts 8 containers
cd backend && python -m venv .venv && .venv/Scripts/python -m pip install -e ".[test]"
.venv/Scripts/python -m pytest -q                # unit tests
.venv/Scripts/python -m resiliencepilot observe  # live freshness
.venv/Scripts/python -m resiliencepilot baseline -n 60
.venv/Scripts/python -m resiliencepilot run --duration 10 --approved-by "<your name>"
../scripts/kill-switch.sh                        # emergency stop (works even if the backend is dead)
```
Two-minute demo: `docs/demo/DAY1_DEMO.md`. Start here for engineering: `CLAUDE.md`.
