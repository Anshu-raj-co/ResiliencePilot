# AI provider layer (advisory)

`rp advise [--dir DIR] [--offline] [--json] [--out FILE]` runs two advisors over stored run records:

- **AI Planner**: proposes an order for the allowlisted experiments, with a technical rationale.
- **AI Analyst**: maps each finding (`rp findings`) to a root cause and a ranked remediation playbook (verify, mitigate, fix).

Code: `backend/resiliencepilot/ai.py`. Config: `config/ai.json` (local Ollama, `qwen2.5:7b`, temperature 0, seed 42, 240 s timeout).
Tests: `backend/tests/test_ai.py`.

## Safety boundary (CLAUDE.md rule 8)

| The AI gets | The AI never gets |
|---|---|
| Rule-based plan entries (ids, evidence counts, rationale) | Shell, Docker socket, `docker_cli`, fault adapters |
| Findings (rule, run ids, measured details, operator notes) | Safety controller, lock, latch or reset |
| A curated, labelled fact list from `reports/day2/README.md` | Credentials, environment variables, raw inspect data |

- `ai.py` imports only `json`, `dataclasses`, `pathlib`, `urllib.parse` and `httpx`; a test parses its imports and fails
  on anything else.
- The Ollama URL must be loopback (`127.0.0.1`, `localhost`, `::1`); anything else is refused at construction.
- The planner can only **reorder** the rule-based candidates. The answer must be an exact permutation of their ids;
  commands and durations always come from the rule-based entry, and every entry still needs a human approver.
- Output is text for a human. Nothing in it is executed.

## Validation and fallback

The deterministic rule-based answer (`rp plan` order; the analyst's knowledge base) is always computed first.

| Check | On failure |
|---|---|
| Ollama reachable, HTTP 200, JSON content (structured output via `format`) | whole answer → rule-based, reason recorded |
| Planner answer is an exact permutation of allowlisted candidate ids | whole plan → rule-based |
| Analyst item: known finding id, cites only that finding's runs, valid confidence, 1–8 playbook steps of kind verify/mitigate/fix | that item → rule-based |
| **Grounding**: the root cause names the component the verified evidence points to, does not blame a component the evidence rules out, hedges the unverified DNS-caching hypothesis, and respects operator annotations | that item → rule-based; the reason and the model's text are kept in the report |

Every result records its provider (`ollama:<model>` or `rule-based`) per section and per finding.

## Measured on 2026-10-11 (macOS, Ollama 0.40.2, `qwen2.5:7b`, 17 committed Day-2 runs)

- **Planner:** valid permutation every time; same order as the rule-based planner (dependency_failure, service_crash,
  telemetry_stall). Rationales are generic. About 1 min 50 s per `rp advise` call (planner + analyst).
- **Analyst, first attempt (all facts in one list, structural validation only):** 7/7 items passed validation, but 4
  root causes contradicted the evidence (DNS caching blamed for the HTTP-200-while-stale symptom; the forwarder
  blamed for data loss that the consumer causes; the server blamed for the consumer's silent failure; a deliberate
  guard test called a cascade). Those four texts are now regression tests.
- **Analyst, with per-finding facts + grounding check:** 1/7 accepted (data loss → fms-consumer drops writes); 6/7
  rejected and replaced by the rule-based answer. The model mostly copied a single fact verbatim, including the
  unverified DNS hypothesis stated as fact. Report: `reports/day2/ai_advice_qwen2.5-7b.md`; offline equivalent:
  `reports/day2/ai_advice_offline.md`.

### `qwen2.5-coder:14b` (2026-10-11, same Mac: Apple Mac16,12, 16 GB, Ollama 0.40.2, 21 committed runs)
- **Planner:** valid permutation, same order as the rules; about 4 min for the planner call alone.
- **Analyst:** timed out at 120 s and again at 300 s (an 8 min 22 s `rp advise` call). Both times every finding fell
  back to the rule-based answer, with `ollama unreachable: ReadTimeout` recorded. On this hardware the 14B model is
  too slow for the analyst prompt, so `config/ai.json` stays on `qwen2.5:7b`.
- **Side effect:** right after the 14B runs, the 7B analyst also timed out twice at 120 s (the 9 GB model was still
  being unloaded on a 16 GB machine, and the prompt had grown to 21 runs). With `timeout_s` 240, the 7B analyst
  completed in about 3 min: 1/7 accepted (data loss → fms-consumer), 6/7 rejected by the grounding check, the same
  result as with 17 runs.

**Conclusion:** with a 7B local model, the value is in the deterministic layer; the AI adds little and is held to
the evidence. A larger local model (`qwen2.5-coder:14b`, ~9 GB) can be tried by changing `model` in `config/ai.json`;
the same validation applies.
