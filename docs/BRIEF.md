# ResiliencePilot SDV — Original Brief (verbatim)

> Saved verbatim from the engineering lead's kickoff message on 2026-10-10.
> The **OVERRIDES** section at the bottom takes priority over everything above it.

---

# RESILIENCEPILOT SDV — MASTER AUTONOMOUS BUILD DIRECTIVE

You are the principal product architect, senior software engineer, chaos-engineering researcher, AI engineer, DevOps engineer, QA lead, and technical team lead for ResiliencePilot SDV.

Your mission is to independently take this project from an empty folder to a working, tested, reproducible, hackathon-ready product.

Do not merely create a plan, explain what I should do, or generate disconnected boilerplate. Inspect the environment, create the files, research the actual system, implement the application, run commands, test behavior, fix failures, coordinate agents, manage GitHub, and continue through all milestones until the product works or a genuine external blocker prevents progress.

I am the human engineering lead. I want to provide this initial prompt and let you handle the implementation and coordination. Do not require me to send a separate prompt for every phase.

Make reasonable engineering decisions independently, document them, and continue. Ask me for help only when necessary for authorization, an external dependency, an important product decision, or a blocker you cannot resolve safely.

---

# 1. PRODUCT VISION

Build ResiliencePilot SDV, a local-first resilience-engineering workbench for a real, publicly available Eclipse Software Defined Vehicle fleet-management application or blueprint.

## The problem

A service can appear healthy while its data is stale or incomplete. For example, a vehicle-telemetry API may return HTTP 200 even though telemetry timestamps have stopped advancing.

Traditional health checks may report success while the actual information is no longer useful.

ResiliencePilot must detect this difference using real observations, perform controlled failure experiments, verify recovery, and generate evidence-backed findings.

## Our differentiator

This is not simply a dashboard, chatbot, or generic chaos-testing wrapper.

Our core workflow is:

1. Connect to a verified local SDV application.
2. Discover and document its service dependencies and real telemetry path.
3. Collect a normal operating baseline.
4. Have an AI analyst recommend informative, approved experiments.
5. Present the hypothesis, target, expected symptoms, duration, and risks.
6. Require approval before an experiment executes.
7. Introduce a bounded, reversible fault through a validated adapter.
8. Measure actual behavior during the fault.
9. Roll back the fault and independently verify recovery.
10. Compare predicted symptoms against actual results.
11. Produce ranked findings, supporting evidence, and practical remediation guidance.
12. Save experiment history and export a reproducible report.

A successful demo must prove that an API or service can appear healthy while telemetry is stale, detect the discrepancy, perform a controlled experiment, verify recovery, and explain what the measurements actually demonstrate.

---

# 2. FIRST: INSPECT THE ENVIRONMENT

Before implementing anything, inspect:

- The current working directory and whether it is empty.
- Existing source files, Git state, branches, commits, and remotes.
- The installed Claude Code version and available collaboration capabilities.
- Git, GitHub CLI, Docker, Docker Compose, Python, Node.js, and other relevant tools.
- GitHub authentication and repository permissions.
- The official Eclipse SDV Fleet Management Blueprint documentation, repository, license, revision, and supported setup.
- The actual Compose files, services, ports, networks, environment variables, health checks, volumes, and dependencies.
- The real telemetry producer-to-consumer path.
- Available timestamps, sequence identifiers, freshness indicators, and completeness signals.
- Whether a proposed fault-injection mechanism affects the real data path.

Use primary sources and inspect actual configuration files. Record exact paths, relevant source URLs, and repository revisions.

Distinguish verified facts, inferences, assumptions, and unresolved questions.

Do not invent services, endpoints, dependencies, telemetry fields, or results.

If the original blueprint is too resource-intensive or a required component cannot be used, explain the limitation and select the smallest technically honest alternative that preserves the core demonstration.

Do not silently replace the real integration with a mock.

---

# 3. CREATE THE COMPLETE PROJECT STRUCTURE

Create the required files and directories yourself.

Adapt the structure to the verified technology stack, but include equivalent locations for:

- CLAUDE.md
- README.md
- .gitignore
- .env.example, if configuration requires environment variables
- docs/ARCHITECTURE.md
- docs/ROADMAP.md
- docs/DECISIONS.md
- docs/recon.md
- docs/testing/
- docs/demo/
- docs/coordination/CURRENT_STATUS.md
- docs/coordination/TASK_BOARD.md
- docs/coordination/HANDOFF.md
- contracts/
- config/
- backend/
- backend/tests/
- frontend/
- experiments/
- reports/
- scripts/

Do not create empty files merely to satisfy a checklist. Populate them with useful content as their purpose becomes clear.

CLAUDE.md must contain durable engineering instructions, product context, testing requirements, safety rules, Git workflow, and continuation instructions.

CURRENT_STATUS.md must record the current milestone, last known-good commit, verified functionality, test results, active tasks, blockers, and next actions.

HANDOFF.md must let a fresh Claude Code session continue without relying on the previous conversation.

Keep documentation consistent with the implementation.

---

# 4. USE AGENT TEAMS AND PARALLEL ENGINEERING

Check whether this Claude Code installation supports Agent Teams and whether the feature is enabled.

If Agent Teams is available, act as the team lead and create a coordinated team of specialist agents.

Assign these responsibilities:

## Agent A — Architecture and SDV Research
- Research the actual Eclipse SDV implementation.
- Verify the data path and service dependencies.
- Identify usable telemetry signals and safe fault targets.
- Document findings and shared interface contracts.

## Agent B — Backend and Experiment Orchestration
- Implement typed backend interfaces, experiment lifecycle, APIs, persistence, and orchestration.
- Coordinate with the architecture agent before locking interfaces.

## Agent C — Safety and Fault Injection
- Implement the deterministic safety controller, watchdog, kill switch, allowlisted fault adapters, rollback, and recovery gate.
- Review the failure paths independently.

## Agent D — Telemetry and Evidence
- Implement baseline collection, freshness verification, sequence progression, completeness where defensible, timing metrics, and evidence records.

## Agent E — AI Analysis, UI, and QA
- Design evidence-driven AI planning and findings.
- Develop test coverage and review failure cases.
- Implement the dashboard after the backend contracts are stable.

As team lead:

- Assign explicit tasks, dependencies, and file ownership.
- Have agents communicate directly through supported team messaging mechanisms.
- Use the shared task list and agent reports to coordinate progress.
- Allow independent research and test design to proceed in parallel.
- Establish shared contracts before parallel implementation of dependent modules.
- Prevent concurrent conflicting edits.
- Integrate and review all contributions.
- Require evidence and test results from every specialist.
- Do not assume ordinary Claude sessions share conversation history or automatically communicate.

If Agent Teams is unavailable, use supported subagents and/or isolated Git worktrees with feature branches. Continue working rather than abandoning the project.

Use parallelism only when it reduces execution time without introducing interface or integration conflicts.

---

# 5. ARCHITECTURE AND COMPONENT BOUNDARIES

Design and implement a coherent system containing:

1. **SDV adapter:** connects to the real local application and exposes verified health and telemetry observations.
2. **Dependency model:** records service relationships and confidence levels.
3. **Telemetry verifier:** determines freshness and progression using actual fields.
4. **Baseline collector:** records repeated observations under normal conditions.
5. **Experiment orchestrator:** manages the experiment lifecycle and timestamps.
6. **Safety controller:** validates requests and controls all permitted fault operations.
7. **Fault adapters:** implement approved, bounded, reversible experiments.
8. **Watchdog and emergency stop:** independently enforce timeouts and cancellation.
9. **Recovery verifier:** determines whether the system has actually recovered.
10. **Evidence store:** persists observations, actions, predictions, rollback status, and results.
11. **AI analyst:** proposes approved experiments and explains measured outcomes.
12. **Frontend:** presents the real backend state and lets engineers inspect and control experiments.
13. **Report exporter:** produces reproducible experiment evidence.

Choose technology based on the actual integration and available environment. Prefer a maintainable, typed implementation and a simple local setup over unnecessary frameworks.

Document component interfaces and strict schemas before building dependent modules.

---

# 6. NON-NEGOTIABLE SAFETY REQUIREMENTS

All fault experiments must be restricted to a local, disposable sandbox.

- Never target real vehicles, production services, or external customer systems.
- Never provide the LLM with unrestricted shell execution, Docker socket access, or credentials.
- Never let the LLM directly perform fault injection, rollback, or recovery decisions.
- Allow only predefined experiment types and validated target identifiers.
- Validate every experiment request against a strict schema.
- Permit only one active experiment at a time.
- Enforce a maximum experiment duration of 30 seconds, or a shorter limit where required.
- Implement a deterministic watchdog independent of the AI and frontend.
- Provide a manual emergency-stop mechanism.
- Roll back on cancellation, timeout, configured safety-threshold violations, or loss of reliable observation.
- Use reliable, advancing telemetry for recovery verification where the integration provides those signals.
- Do not declare recovery based solely on HTTP 200 or a running container.
- If rollback or recovery cannot be verified, enter a latched unsafe state and prevent further experiments until safely reset.
- Make cleanup robust against exceptions and interrupted execution.
- Record the experiment target, request, timestamps, fault actions, stop reason, rollback outcome, and recovery evidence.
- Test negative paths and failure scenarios.
- Do not claim a fault was successfully injected unless the intended mechanism and target were actually verified.
- Do not claim recovery unless the recovery criteria passed.
- Do not fabricate measurements, tests, findings, or results.

Keep the safety controller small, deterministic, and independently tested.

---

# 7. IMPLEMENTATION PHASES — EXECUTE ALL AUTOMATICALLY

Treat the following phases as acceptance gates, not separate prompts requiring me to return and instruct you.

## PHASE 0 — Reconnaissance and specification

- Research the real SDV application.
- Verify license, configuration, dependencies, telemetry path, and fault-injection feasibility.
- Create the architecture, decisions, contracts, task board, and roadmap.
- Identify resource constraints and external blockers.

Proceed when the integration approach and interfaces are grounded in evidence.

## PHASE 1 — Real SDV pipeline and telemetry baseline

- Start the selected SDV services locally.
- Verify the real telemetry path.
- Collect repeated baseline observations.
- Implement freshness and sequence-progression verification using actual available fields.
- Measure latency and completeness only when the definitions are defensible.
- Represent unavailable signals explicitly.
- Add unit and integration tests.
- Document clean-start commands.

Acceptance criteria:
- The local SDV integration runs.
- Actual telemetry is observed.
- The baseline contains real measurements.
- Stale/non-advancing telemetry is detected by tested logic.
- Setup is reproducible.

## PHASE 2 — Safety controller and first experiment

Implement:
- Experiment state machine.
- Strict request and target validation.
- One-active-experiment lock.
- Hard duration limit.
- Watchdog and emergency stop.
- Rollback and recovery verification.
- Auditable experiment timeline.

Implement the telemetry-stall experiment first, using the verified data path and a reversible, sandboxed mechanism.

Test success, timeout, cancellation, invalid target, malformed request, concurrent request, observer loss, rollback failure, and recovery failure.

Acceptance criteria:
- The fault affects the verified path.
- The system records actual impact.
- Rollback occurs.
- Recovery is independently verified.
- Failure cases block unsafe continuation.

## PHASE 3 — Additional experiments

Implement service crash/restart and bounded network degradation where the actual architecture supports them.

Do not assume a proxy is on the telemetry path merely because it exists in Compose.

For every experiment:
- Record a hypothesis.
- Capture baseline, fault-active, and recovery observations.
- Record stop reason and rollback outcome.
- Calculate time-to-detection and time-to-recovery only when timestamps support those metrics.
- Repeat runs when practical.
- Report variability and limitations honestly.

If a network experiment cannot be justified, implement the strongest verified alternative and document why.

## PHASE 4 — Evidence-driven AI analyst

Implement an advisory AI planner and findings engine.

The planner must:
- Rank only approved, predefined experiments.
- Use verified dependencies, baseline observations, prior outcomes, and diagnostic value.
- Explain expected symptoms, risks, and hypotheses.
- Produce schema-validated structured output.
- Require human approval before execution.

The findings engine must:
- Compare predicted symptoms against measured outcomes.
- Reference actual evidence.
- Rank findings using defensible criteria.
- Explain likely causes without overstating certainty.
- Recommend registered remediation steps or runbooks.
- Distinguish confirmed findings, hypotheses, and unknowns.
- Fall back to deterministic reports when the model is unavailable or returns invalid output.

Test model timeouts, malformed JSON, unknown enums, missing evidence, contradictory observations, and prompt injection.

Do not train a custom model unless there is a compelling, documented need. Do not allow the AI to override the safety controller.

## PHASE 5 — Dashboard and reporting

Build a usable dashboard backed by the real backend.

It should present:
- Service/dependency graph and confidence labels.
- System readiness and telemetry baseline.
- AI-ranked experiment proposals.
- Hypotheses, targets, and risks.
- Human approval controls.
- Live experiment state and emergency stop.
- Fault status, rollback status, and recovery verification.
- Predicted-versus-observed comparisons.
- Evidence-backed findings and remediation.
- Experiment history and report export.

Use real backend state. Do not use fabricated live metrics or simulated success in the final demonstration.

Test UI/backend integration and ensure no UI action can bypass the safety controller.

## PHASE 6 — Integration and hackathon readiness

- Run the system from a clean setup.
- Execute each supported experiment end-to-end.
- Verify rollback and recovery.
- Run all relevant unit, integration, and end-to-end tests.
- Test failure paths.
- Fix integration defects.
- Update installation, architecture, testing, troubleshooting, and limitations documentation.
- Prepare a concise demonstration script.
- Generate screenshots and logs only from real runs.
- Record exact setup and demo commands.
- Report unfinished requirements honestly.

The final demo should tell one coherent story: hidden telemetry failure, baseline, AI hypothesis, approved experiment, measured impact, verified recovery, and evidence-backed analysis.

Do not claim production readiness or automotive safety certification.

---

# 8. GITHUB AND VERSION CONTROL

Inspect the existing repository before changing it.

If the directory is empty and no repository exists:

- Initialize Git.
- Create a suitable .gitignore.
- Keep secrets, environment files, caches, local environments, generated artifacts, and sensitive logs out of Git.
- Check GitHub CLI authentication.
- If authenticated and permitted, create a private repository named resiliencepilot-sdv.
- Commit the initial project structure and instructions.
- Push the initial branch.

If a repository already exists, preserve its history and configuration. Never overwrite it just to simplify setup.

Use a feature branch for each logical unit of work.

For each completed milestone:
1. Run relevant tests.
2. Inspect the diff.
3. Commit a coherent change.
4. Push the branch.
5. Create a reviewable pull request when possible.
6. Summarize changes, tests, reproduction steps, and limitations.
7. Merge only after the relevant checks and review requirements pass.

Keep main stable. Do not force-push or commit credentials.

If GitHub authentication or an approval blocks an operation, tell me exactly what action is needed and continue with work that does not depend on that blocker.

---

# 9. FIVE HUMAN TEAMMATES

Structure the project so five people can contribute independently.

Recommended ownership:
- Human 1: engineering lead, integration, contracts, GitHub review, milestone acceptance, demo.
- Human 2: SDV integration, service mapping, setup documentation.
- Human 3: safety controller, fault adapters, rollback tests.
- Human 4: telemetry verification, baseline, metrics, evidence.
- Human 5: AI analysis, dashboard, QA, reporting.

Document ownership, dependencies, acceptance criteria, and file boundaries in TASK_BOARD.md.

If human teammates are not yet connected to the repository, do not invent their identities or assume their GitHub access. Prepare the task board and clear handoff instructions so they can join later.

---

# 10. SESSION CONTINUITY AND SUBSCRIPTION HANDOFF

Assume the current Claude session or subscription may end before the project is finished.

After each milestone:
- Update CURRENT_STATUS.md.
- Update TASK_BOARD.md.
- Update HANDOFF.md.
- Record the last known-good commit SHA and current branch.
- Record exactly which acceptance criteria passed.
- Record commands and tests actually executed, including failures.
- Record active PRs, uncommitted changes, blockers, and unresolved decisions.
- State the next concrete task and its acceptance criteria.
- Commit and push the handoff when safe.

A new Claude session must be able to resume by reading repository instructions and status files.

Do not rely on unsaved conversation memory.

If another teammate or authorized Claude account takes over, preserve repository history and use the same documented workflow. Never request or share another person's account credentials.

---

# 11. AUTONOMOUS WORKING RULES

Do not stop after creating documentation or generating an architecture proposal.

Do not wait for me to send a separate prompt for Phase 1, Phase 2, Phase 3, Phase 4, Phase 5, or Phase 6.

Once a phase's acceptance criteria are satisfied, proceed automatically to the next phase.

If an acceptance criterion fails:
- Inspect the actual failure.
- Diagnose and fix it where safe.
- Rerun the relevant test.
- Document unresolved problems.
- Continue with independent tasks that can safely proceed.

If a genuine blocker requires my intervention, explain the exact issue and the smallest action I need to take. Do not repeatedly ask for permission for routine, reversible development tasks that fall within the available tool permissions.

Before destructive actions, external changes, or operations with material security implications, pause and obtain appropriate authorization.

At each milestone, report:
1. What works now.
2. What was actually tested.
3. Test results and remaining failures.
4. Relevant commits and pull requests.
5. Remaining risks and blockers.
6. The next milestone.

Never fabricate successful builds, tests, integrations, GitHub pushes, or agent communications.

---

# 12. BEGIN NOW

Start by inspecting the current directory, environment, repository, available Claude Code collaboration features, and GitHub authentication.

Then create the project files, establish the product specification, verify the actual Eclipse SDV integration, and execute Phase 0.

Use Agent Teams when genuinely available. Delegate independent work, coordinate through supported messaging, establish contracts, and integrate the results.

Continue through every implementation phase without waiting for another prompt from me.

Prioritize a small, real, end-to-end working system over a large collection of unfinished features.

The objective is a reproducible, tested, evidence-driven ResiliencePilot SDV product that demonstrates hidden telemetry failure detection, controlled resilience experiments, verified recovery, and AI analysis grounded in actual observations.

BEGIN EXECUTION NOW.

---

# OVERRIDES (these take priority over everything above)

1. First action: save this entire message into docs/BRIEF.md so any new session can read it. Then create CLAUDE.md, .gitignore and the docs/coordination files.
2. Scope for this session: Day 1 only. Stop after Phase 2 (verified SDV pipeline, baseline, one safe fault, kill switch tested). Do not start Phases 3-6; list them in docs/ROADMAP.md.
3. Do NOT use Agent Teams. Use a single session; subagents only for safety review.
4. Time box: if the SDV stack is not running end to end after 60 minutes, stop and tell me what failed. Do not silently switch to a mock.
5. Do not start the Docker stack until you have written docs/recon.md and told me the memory available from `docker info`.
6. Checkpointing: commit and push to branch wip/<topic> every 20-30 minutes and update docs/coordination/HANDOFF.md each time. Never leave work uncommitted.
7. If no GitHub remote exists, run: gh repo create resiliencepilot-sdv --private --source . --push
8. End the session with a scripted 2-minute Day 1 demo saved in docs/demo/ (app running, baseline, one safe fault, kill switch, recovery).
9. Fault-injection claims: if traffic does not truly pass through the proxy, say so and use container stop or provider pause instead.
