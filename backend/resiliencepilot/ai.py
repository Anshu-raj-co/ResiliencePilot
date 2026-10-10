"""Advisory AI layer: AI Planner and AI Analyst on a local Ollama model, with a deterministic rule-based fallback.

Isolation (safety rule 8): this module receives only derived, read-only data (plan entries, findings, catalog text,
a curated fact list). It imports nothing that can touch Docker, the shell, the safety controller or fault adapters
(enforced by tests/test_ai.py), the Ollama URL must be loopback, and its output is text for a human. The AI can only
REORDER allowlisted candidates the rule-based planner produced; it cannot add experiments, change durations or
commands, approve, inject, roll back or decide recovery. Every answer is validated; anything invalid falls back to the
rule-based answer, and every result records which provider produced it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MAX_TEXT = 600

# Curated facts from reports/day2/README.md, each labelled. The model sees only these, nothing else.
FACTS = [
    ("verified", "fms-server answers HTTP 200 with the last stored status while the forwarder is paused or stopped; "
                 "receivedDateTime is the request time, so only createdDateTime shows the data is stale"),
    ("verified", "fms-forwarder ignores SIGTERM: docker stop always waits the 2 s grace and kills it (exit 137)"),
    ("verified", "with InfluxDB down, fms-server requests first block ~4 s, then fail fast with HTTP 500"),
    ("verified", "after InfluxDB is back, recovery takes 20-27 s; in some runs the API stays HTTP 200 with frozen "
                 "createdDateTime (silent), in others it answers HTTP 500 (visible)"),
    ("verified", "fms-consumer drops statuses it cannot write ('failed to write data to influx'); no buffering or backfill"),
    ("verified", "createdDateTime advances every 1.0 s at the API although the forwarder publishes every 0.5 s"),
    ("hypothesis", "the isahc/libcurl HTTP client caches failed DNS lookups, delaying recovery after InfluxDB restarts"),
    ("hypothesis", "during that time fms-consumer (silent case) or fms-server (visible case) kept failing to resolve "
                   "InfluxDB's host name; reported from container logs, log excerpts not committed"),
]


# Which facts are relevant to which finding rule (indices into FACTS); a narrow context keeps a small model on track.
RELEVANT_FACTS = {"silent_during_fault": [0, 1, 5], "silent_after_rollback": [3, 7, 6], "not_recovered": [3, 7, 6],
                  "slow_recovery": [2, 3, 7, 6], "data_loss": [4, 2], "blast_radius": []}

# Deterministic grounding check for AI root causes, per (rule, experiment type or "*"):
#   any  : the text must name at least one of these (the component the verified evidence points to)
#   none : the text must not name any of these (components the verified evidence rules out)
GROUNDING = {
    ("silent_during_fault", "*"): {"any": ["fms-server", "stored status", "last status", "createdDateTime"],
                                   "none": ["dns", "resolv"]},
    ("silent_after_rollback", "*"): {"any": ["consumer"], "none": []},
    ("not_recovered", "*"): {"any": ["consumer", "resolv", "dns"], "none": []},
    ("slow_recovery", "*"): {"any": ["fms-server", "resolv", "dns"], "none": []},
    ("data_loss", "*"): {"any": ["consumer"], "none": ["forwarder"]},
    ("blast_radius", "*"): {"any": [], "none": []},
}
HEDGES = ("hypothes", "possibl", "likely", "candidate", "may ", "might", "suspect", "not verified", "unverified")


def grounding_problem(rule: str, etype: str, text: str, notes: list[str]) -> str | None:
    """Why an AI root cause contradicts the recorded evidence, or None if it is consistent with it."""
    t = text.lower()
    g = GROUNDING.get((rule, etype)) or GROUNDING.get((rule, "*")) or {"any": [], "none": []}
    if g["any"] and not any(k.lower() in t for k in g["any"]):
        return f"does not name the component the evidence points to (one of {g['any']})"
    hit = next((k for k in g["none"] if k.lower() in t), None)
    if hit:
        return f"blames {hit!r}, which the verified evidence rules out for this finding"
    if "cach" in t and not any(h in t for h in HEDGES):
        return "states DNS caching as fact; it is an unverified hypothesis"
    if any(k in t for k in ("resolv", "dns")) and not any(h in t for h in HEDGES):
        return "states the name-resolution cause as fact; it is a hypothesis (log excerpts not committed)"
    if notes and not any(k in t for k in ("operator", "deliberate", "test", "annotat")):
        return f"ignores the run annotation ({notes[0]})"
    return None


@dataclass(frozen=True)
class AIConfig:
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:7b"
    timeout_s: float = 120.0
    temperature: float = 0.0
    seed: int = 42


def load_ai_config(root: Path) -> AIConfig:
    p = root / "config" / "ai.json"
    raw = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    return AIConfig(**{k: raw[k] for k in ("base_url", "model", "timeout_s", "temperature", "seed") if k in raw})


class AIUnavailable(RuntimeError):
    pass


class OllamaProvider:
    """POST /api/chat with a JSON schema in `format` (structured output). Loopback only."""

    def __init__(self, cfg: AIConfig, transport: httpx.BaseTransport | None = None) -> None:
        host = urlparse(cfg.base_url).hostname
        if host not in LOOPBACK:
            raise ValueError(f"AI base_url must be loopback (local Ollama), got host {host!r}")
        self.cfg, self.transport = cfg, transport
        self.name = f"ollama:{cfg.model}"
        # Every call, in order: exact prompts, raw HTTP response body, model and the validation result (set by the
        # caller). Written to data/ai_calls/ by `rp advise` so any AI text can be traced back to what the model saw.
        self.calls: list[dict] = []

    def chat_json(self, system: str, user: str, schema: dict) -> dict:
        body = {"model": self.cfg.model, "stream": False, "format": schema,
                "options": {"temperature": self.cfg.temperature, "seed": self.cfg.seed},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        call = {"provider": self.name, "model": self.cfg.model, "base_url": self.cfg.base_url,
                "options": body["options"], "system_prompt": system, "user_prompt": user, "schema": schema,
                "http_status": None, "raw_response": None, "error": None, "validation": None}
        self.calls.append(call)
        try:
            with httpx.Client(timeout=self.cfg.timeout_s, transport=self.transport) as c:
                r = c.post(self.cfg.base_url.rstrip("/") + "/api/chat", json=body)
        except httpx.HTTPError as exc:
            call["error"] = f"ollama unreachable: {type(exc).__name__}: {exc}"
            raise AIUnavailable(call["error"]) from exc
        call["http_status"], call["raw_response"] = r.status_code, r.text
        if r.status_code != 200:
            call["error"] = f"ollama HTTP {r.status_code}: {r.text[:200]}"
            raise AIUnavailable(call["error"])
        try:
            return json.loads(r.json()["message"]["content"])
        except (KeyError, TypeError, ValueError) as exc:
            call["error"] = f"ollama returned non-JSON content: {exc}"
            raise AIUnavailable(call["error"]) from exc


def _validated(provider, result: str) -> None:
    """Record the validation outcome on the provider's latest call log entry."""
    if provider is not None and getattr(provider, "calls", None):
        provider.calls[-1]["validation"] = result


def _text(v) -> str:
    return " ".join(str(v).split())[:MAX_TEXT]


# ----------------------------------------------------------------------------------------------------------------- planner
PLAN_SCHEMA = {"type": "object", "required": ["order", "summary"], "properties": {
    "summary": {"type": "string"},
    "order": {"type": "array", "items": {"type": "object", "required": ["id", "rationale"],
                                         "properties": {"id": {"type": "string"}, "rationale": {"type": "string"}}}}}}

PLAN_SYSTEM = ("You are an advisory reliability engineer for a local test sandbox of the Eclipse SDV Fleet Management "
               "Blueprint. You may ONLY reorder the candidate experiments given to you, by id. You cannot add, remove or "
               "modify experiments, durations or commands, and a human approves every run. Put the most informative and "
               "riskiest experiment first. Give a short technical rationale per candidate that cites the evidence numbers "
               "provided. Answer with JSON only.")


def _cid(e: dict) -> str:
    return f"{e['experiment_type']}@{e['target']}"


def plan_prompt(entries: list[dict], findings: list[dict]) -> str:
    cands = [{"id": _cid(e), "mechanism": e["mechanism"], "rule_based_rank": e["rank"], "priority": e["priority"],
              "evidence": e["evidence"], "rationale": e["rationale"]} for e in entries]
    fs = [{"rule": f["rule"], "level": f["level"], "experiment": f["experiment_type"], "runs": len(f["runs"])}
          for f in findings]
    return json.dumps({"candidates": cands, "findings": fs, "facts": [f"[{k}] {t}" for k, t in FACTS]}, indent=1)


def validate_plan(answer: dict, entries: list[dict]) -> list[dict]:
    """Return entries reordered per the answer; raise ValueError unless the answer is an exact permutation."""
    order = answer.get("order") if isinstance(answer, dict) else None
    if not isinstance(order, list):
        raise ValueError("answer has no 'order' list")
    by_id = {_cid(e): e for e in entries}
    ids = [o.get("id") if isinstance(o, dict) else None for o in order]
    if sorted(map(str, ids)) != sorted(by_id) or len(set(ids)) != len(ids):
        raise ValueError(f"order must be a permutation of {sorted(by_id)}, got {ids}")
    out = []
    for rank, o in enumerate(order, 1):
        e = by_id[o["id"]]
        out.append({"rank": rank, "id": o["id"], "experiment_type": e["experiment_type"], "target": e["target"],
                    "rule_based_rank": e["rank"], "rationale": _text(o.get("rationale", "")),
                    "command": e["command"], "requires_human_approval": True})
    return out


def rule_based_plan(entries: list[dict]) -> list[dict]:
    return [{"rank": e["rank"], "id": _cid(e), "experiment_type": e["experiment_type"], "target": e["target"],
             "rule_based_rank": e["rank"], "rationale": "; ".join(e["rationale"]), "command": e["command"],
             "requires_human_approval": True} for e in entries]


def ai_plan(entries: list[dict], findings: list[dict], provider: OllamaProvider | None) -> dict:
    fallback = None
    if provider is not None:
        try:
            ans = provider.chat_json(PLAN_SYSTEM, plan_prompt(entries, findings), PLAN_SCHEMA)
            order = validate_plan(ans, entries)
            _validated(provider, "accepted: exact permutation of the allowlisted candidates")
            return {"provider": provider.name, "advisory": True, "summary": _text(ans.get("summary", "")),
                    "order": order}
        except (AIUnavailable, ValueError) as exc:
            fallback = str(exc)
            _validated(provider, f"rejected, rule-based order used: {exc}")
    return {"provider": "rule-based", "fallback_reason": fallback or "AI disabled", "advisory": True,
            "summary": "Rule-based order (priority = 0.6 x information + 0.4 x risk).", "order": rule_based_plan(entries)}


# ----------------------------------------------------------------------------------------------------------------- analyst
ANALYSIS_SCHEMA = {"type": "object", "required": ["analyses"], "properties": {"analyses": {"type": "array", "items": {
    "type": "object", "required": ["finding", "root_cause", "confidence", "evidence_runs", "playbook"],
    "properties": {"finding": {"type": "string"}, "root_cause": {"type": "string"},
                   "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                   "evidence_runs": {"type": "array", "items": {"type": "string"}},
                   "playbook": {"type": "array", "items": {"type": "object", "required": ["kind", "step"], "properties": {
                       "kind": {"type": "string", "enum": ["verify", "mitigate", "fix"]}, "step": {"type": "string"}}}}}}}}}

ANALYSIS_SYSTEM = ("You are an advisory reliability engineer analysing measured fault-injection results from a local "
                   "sandbox of the Eclipse SDV Fleet Management Blueprint. For each finding id, state the most likely root "
                   "cause using ONLY that finding's own facts and runs; name the component the facts name; say "
                   "'hypothesis' when you rely on a fact labelled hypothesis; if a run has an operator_note, take it "
                   "into account. "
                   "Cite only run ids that appear in that finding. Give a ranked remediation playbook (verify, mitigate, "
                   "fix steps). You never execute anything. Answer with JSON only.")

# Deterministic analyst: (rule, experiment_type or "*") -> root cause, confidence, playbook
KB = {
    ("silent_during_fault", "*"): (
        "fms-server serves the last stored status with receivedDateTime = request time and no staleness signal, so the "
        "API stays HTTP 200 while vehicle data is frozen (verified).", "high",
        [("verify", "Compare createdDateTime age with the 5 s threshold on every poll (`rp observe`)."),
         ("mitigate", "Alert on createdDateTime age > 5 s instead of on HTTP status or container state."),
         ("fix", "Expose data age in the rFMS response or return 503 when the newest status is older than a threshold.")]),
    ("silent_after_rollback", "dependency_failure"): (
        "After InfluxDB returned, the API kept answering 200 with frozen data for ~21-24 s (verified from run records). "
        "Hypothesis: fms-consumer kept failing to resolve InfluxDB's host name (reported from container logs, excerpts "
        "not committed), so it wrote nothing. Candidate mechanism: failed-DNS caching in "
        "the isahc/libcurl client (hypothesis, not verified).", "medium",
        [("verify", "Correlate `docker logs fms-consumer` 'failed to resolve host name' timestamps with InfluxDB healthy time."),
         ("mitigate", "Gate readiness on fresh createdDateTime, not on HTTP 200 or container running."),
         ("fix", "Disable or shorten the HTTP client's DNS cache (e.g. CURLOPT_DNS_CACHE_TIMEOUT) or re-resolve on connect errors.")]),
    ("not_recovered", "dependency_failure"): (
        "Recovery criteria were not met within 30 s after InfluxDB returned (verified from the run record). Hypothesis: "
        "the consumer was still failing name resolution (log excerpts not committed); the DNS-cache mechanism is a "
        "further hypothesis.", "medium",
        [("verify", "Re-run dependency_failure and record time from InfluxDB healthy to the consumer's first successful write."),
         ("mitigate", "Restart fms-consumer automatically when writes fail for longer than N seconds."),
         ("fix", "Make the consumer re-resolve and retry with backoff instead of reusing a failed lookup.")]),
    ("slow_recovery", "dependency_failure"): (
        "fms-server answered HTTP 500 for ~18-22 s after InfluxDB was back (verified from run records), so recovery took "
        "far longer than InfluxDB's own restart. Suspected cause (hypothesis): name-resolution failures reported from "
        "container logs (excerpts not committed), possibly from DNS caching.", "medium",
        [("verify", "Time InfluxDB healthy -> first fms-server HTTP 200 across 3 runs; check logs for resolve errors."),
         ("mitigate", "Add a readiness probe on fms-server that runs a real query, and retry clients with backoff."),
         ("fix", "Configure the HTTP client to re-resolve host names after connection failures.")]),
    ("data_loss", "*"): (
        "fms-consumer drops statuses it cannot write ('failed to write data to influx'); there is no buffer or backfill "
        "(verified). Loss happens in the fast-fail phase; writes during the blocking phase are delayed, not dropped.", "high",
        [("verify", "Query stored statuses for the outage window (`data_gap` in the run record)."),
         ("mitigate", "Alert on consumer write failures."),
         ("fix", "Add a bounded in-memory/disk write buffer with retry in fms-consumer, or a Zenoh storage for replay.")]),
    ("blast_radius", "*"): (
        "A container outside the experiment's blast radius changed state. Check the run annotation first: the Day-2 "
        "occurrence was a deliberate guard test (operator restarted Grafana).", "low",
        [("verify", "Inspect `docker events` for the run window and the run's annotation."),
         ("mitigate", "Keep the blast-radius guard armed; it latched the run as designed."),
         ("fix", "If not operator-caused, trace the dependency that propagated the fault.")]),
}
KB_SERVICE_CRASH_EXTRA = ("fix", "Handle SIGTERM in fms-forwarder for a graceful shutdown (today docker stop always ends "
                                 "in SIGKILL, exit 137; verified).")


def _kb(rule: str, etype: str):
    return KB.get((rule, etype)) or KB.get((rule, "*"))


def _numbered(findings: list[dict]) -> list[dict]:
    return [{"id": f"F{i}", **f} for i, f in enumerate(findings, 1)]


def rule_based_analysis(findings: list[dict]) -> list[dict]:
    out = []
    for f in _numbered(findings):
        kb = _kb(f["rule"], f["experiment_type"])
        if kb is None:
            cause, conf, steps = "No rule-based root cause for this finding; investigate manually.", "low", []
        else:
            cause, conf, steps = kb[0], kb[1], list(kb[2])
        if f["experiment_type"] == "service_crash" and f["rule"] == "silent_during_fault":
            steps.append(KB_SERVICE_CRASH_EXTRA)
        out.append({"finding": f["id"], "rule": f["rule"], "experiment_type": f["experiment_type"],
                    "root_cause": cause, "confidence": conf, "evidence_runs": [h["run"] for h in f["runs"]],
                    "playbook": [{"rank": i, "kind": k, "step": s} for i, (k, s) in enumerate(steps, 1)],
                    "source": "rule-based"})
    return out


def analysis_prompt(findings: list[dict]) -> str:
    fs = []
    for f in _numbered(findings):
        runs = [{"run": h["run"], "detail": h["detail"], **({"operator_note": h["note"]} if h.get("note") else {})}
                for h in f["runs"]]
        fs.append({"id": f["id"], "rule": f["rule"], "level": f["level"], "experiment": f["experiment_type"],
                   "runs": runs, "facts": [f"[{FACTS[i][0]}] {FACTS[i][1]}" for i in RELEVANT_FACTS.get(f["rule"], [])]})
    return json.dumps({"findings": fs}, indent=1)


def validate_analysis(answer: dict, findings: list[dict], rejected: list | None = None) -> dict[str, dict]:
    """Keep only well-formed, evidence-consistent items for known finding ids that cite that finding's own runs.
    Returns id -> item; reasons for rejected items are appended to `rejected`."""
    rejected = rejected if rejected is not None else []
    items = answer.get("analyses") if isinstance(answer, dict) else None
    if not isinstance(items, list):
        raise ValueError("answer has no 'analyses' list")
    known = {f["id"]: f for f in _numbered(findings)}
    kept: dict[str, dict] = {}
    for it in items:
        if not isinstance(it, dict) or it.get("finding") not in known or it["finding"] in kept:
            continue
        f = known[it["finding"]]
        runs = {h["run"] for h in f["runs"]}
        cited = it.get("evidence_runs") or []
        steps = it.get("playbook") or []
        if (not isinstance(cited, list) or not cited or not set(map(str, cited)) <= runs
                or it.get("confidence") not in ("low", "medium", "high") or not str(it.get("root_cause", "")).strip()
                or not isinstance(steps, list) or not 1 <= len(steps) <= 8
                or any(not isinstance(s, dict) or s.get("kind") not in ("verify", "mitigate", "fix")
                       or not str(s.get("step", "")).strip() for s in steps)):
            rejected.append({"finding": it["finding"], "reason": "malformed item or cites runs outside the finding",
                             "ai_root_cause": _text(it.get("root_cause", ""))})
            continue
        problem = grounding_problem(f["rule"], f["experiment_type"], str(it["root_cause"]),
                                    [h["note"] for h in f["runs"] if h.get("note")])
        if problem:
            rejected.append({"finding": it["finding"], "reason": problem, "ai_root_cause": _text(it["root_cause"])})
            continue
        kept[it["finding"]] = {"finding": it["finding"], "rule": f["rule"], "experiment_type": f["experiment_type"],
                               "root_cause": _text(it["root_cause"]), "confidence": it["confidence"],
                               "evidence_runs": [str(c) for c in cited],
                               "playbook": [{"rank": i, "kind": s["kind"], "step": _text(s["step"])}
                                            for i, s in enumerate(steps, 1)]}
    return kept


def ai_analyse(findings: list[dict], provider: OllamaProvider | None) -> dict:
    base = rule_based_analysis(findings)
    if not findings:
        return {"provider": "rule-based", "advisory": True, "analyses": []}
    reason = "AI disabled"
    if provider is not None:
        try:
            rejected: list[dict] = []
            kept = validate_analysis(provider.chat_json(ANALYSIS_SYSTEM, analysis_prompt(findings), ANALYSIS_SCHEMA),
                                     findings, rejected)
            _validated(provider, {"accepted": sorted(kept), "rejected": [{"finding": r["finding"], "reason": r["reason"]}
                                                                         for r in rejected],
                                  "missing": sorted({b["finding"] for b in base} - set(kept)
                                                    - {r["finding"] for r in rejected})})
            why = {r["finding"]: r for r in rejected}
            merged = [{**kept[b["finding"]], "source": provider.name} if b["finding"] in kept
                      else {**b, "source": "rule-based",
                            "ai_rejected": why[b["finding"]]["reason"] if b["finding"] in why else "no AI answer for this finding",
                            **({"ai_rejected_text": why[b["finding"]]["ai_root_cause"]} if b["finding"] in why else {})}
                      for b in base]
            return {"provider": provider.name, "advisory": True, "ai_items_accepted": len(kept),
                    "ai_items_rejected_or_missing": len(base) - len(kept), "rejected": rejected, "analyses": merged}
        except (AIUnavailable, ValueError) as exc:
            reason = str(exc)
            _validated(provider, f"rejected, rule-based analysis used: {exc}")
    return {"provider": "rule-based", "fallback_reason": reason, "advisory": True, "analyses": base}


def render_markdown(plan_result: dict, analysis: dict) -> str:
    L = ["# AI advisory report", "",
         "Advisory only. Nothing here is executed: every experiment needs a human approver, and injection, rollback and "
         "recovery decisions stay with the deterministic safety controller.", "",
         f"## Proposed experiment order (provider: `{plan_result['provider']}`)", ""]
    if plan_result.get("fallback_reason"):
        L += [f"Fallback reason: {plan_result['fallback_reason']}", ""]
    if plan_result.get("summary"):
        L += [plan_result["summary"], ""]
    for o in plan_result["order"]:
        L += [f"{o['rank']}. **{o['experiment_type']}** on `{o['target']}` (rule-based rank {o['rule_based_rank']})",
              f"   - {o['rationale']}", f"   - `{o['command']}`"]
    L += ["", f"## Root causes and remediation (provider: `{analysis['provider']}`)", ""]
    if analysis.get("fallback_reason"):
        L += [f"Fallback reason: {analysis['fallback_reason']}", ""]
    if not analysis["analyses"]:
        L.append("No findings to analyse.")
    for a in analysis["analyses"]:
        L += [f"### {a['finding']}. {a['rule']} — {a['experiment_type']} (confidence: {a['confidence']}, source: {a['source']})",
              "", a["root_cause"], ""]
        if a.get("ai_rejected"):
            said = f' The model said: "{a["ai_rejected_text"]}"' if a.get("ai_rejected_text") else ""
            L += [f"_AI answer rejected: {a['ai_rejected']}.{said}_", ""]
        L += ["Evidence: " + ", ".join(f"`{r}`" for r in a["evidence_runs"]), "", "Playbook:"]
        L += [f"{s['rank']}. [{s['kind']}] {s['step']}" for s in a["playbook"]]
        L.append("")
    return "\n".join(L) + "\n"
