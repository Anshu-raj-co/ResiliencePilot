"""Advisory AI layer (ai.py): isolation from the safety path, loopback-only provider, strict validation, fallback."""
from __future__ import annotations

import ast
import json

import httpx
import pytest

from resiliencepilot import ai
from resiliencepilot.ai import (AIConfig, OllamaProvider, ai_analyse, ai_plan, grounding_problem, render_markdown,
                                rule_based_analysis)
from resiliencepilot.findings import derive_findings
from resiliencepilot.planner import plan

from test_planner_findings import CATALOG, DEP_MAP, _dep
from test_scoring import STALL, _poll, _rec

ENTRIES = plan(CATALOG, DEP_MAP, [])
IDS = ["dependency_failure@influxDB", "telemetry_stall@fms-forwarder"]
SILENT_AFTER = STALL + [_poll("recovery", 13 + i * 0.5, fresh=False) for i in range(12)]
RUNS = [_rec(STALL, run_id="20261010T180000Z-a"),
        _dep("20261010T180001Z-b", ttr=24.0, data_gap={"max_gap_s": 4.0, "median_interval_s": 0.5})]
FINDINGS = derive_findings(RUNS)
FID = {(f["rule"], f["experiment_type"]): f"F{i}" for i, f in enumerate(FINDINGS, 1)}
STALL_SILENT, DEP_SLOW, DEP_LOSS = (FID[("silent_during_fault", "telemetry_stall")], FID[("slow_recovery", "dependency_failure")],
                                    FID[("data_loss", "dependency_failure")])


def _provider(answer=None, *, status=200, content=None, exc=None, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        if exc is not None:
            raise exc
        body = content if content is not None else json.dumps(answer)
        return httpx.Response(status, json={"message": {"role": "assistant", "content": body}})
    return OllamaProvider(AIConfig(model="qwen2.5:7b"), transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------------------------------------- isolation
def test_ai_module_cannot_reach_docker_shell_or_safety_state():
    tree = ast.parse(open(ai.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    forbidden = {"subprocess", "os", "shutil", "socket", ".safety", ".orchestrator", ".faults", ".docker_cli",
                 ".guardrails", ".api", ".evidence", ".sdv_adapter"}
    assert not imported & forbidden, imported & forbidden
    assert imported <= {"__future__", "json", "dataclasses", "pathlib", "urllib.parse", "httpx"}


@pytest.mark.parametrize("url", ["http://10.0.0.5:11434", "https://api.example.com", "http://ollama.local:11434"])
def test_provider_refuses_non_loopback_urls(url):
    with pytest.raises(ValueError, match="loopback"):
        OllamaProvider(AIConfig(base_url=url))


def test_request_uses_structured_output_and_deterministic_options():
    seen = []
    ai_plan(ENTRIES, FINDINGS, _provider({"summary": "s", "order": [{"id": i, "rationale": "r"} for i in IDS]}, seen=seen))
    body = seen[0]
    assert body["model"] == "qwen2.5:7b" and body["stream"] is False
    assert body["format"] == ai.PLAN_SCHEMA and body["options"] == {"temperature": 0.0, "seed": 42}
    user = json.loads(body["messages"][1]["content"])
    assert {c["id"] for c in user["candidates"]} == set(IDS)
    assert "approved_by" not in body["messages"][1]["content"]


# ------------------------------------------------------------------------------------------------------------ planner
def test_valid_reorder_is_accepted_but_commands_come_from_the_rule_based_plan():
    ans = {"summary": "stall first", "order": [
        {"id": "telemetry_stall@fms-forwarder", "rationale": "cheap baseline; then `docker rm -f influxDB`"},
        {"id": "dependency_failure@influxDB", "rationale": "riskier"}]}
    res = ai_plan(ENTRIES, FINDINGS, _provider(ans))
    assert res["provider"] == "ollama:qwen2.5:7b" and res["advisory"]
    assert [o["id"] for o in res["order"]] == ["telemetry_stall@fms-forwarder", "dependency_failure@influxDB"]
    by_type = {e["experiment_type"]: e for e in ENTRIES}
    for o in res["order"]:
        assert o["command"] == by_type[o["experiment_type"]]["command"] and o["requires_human_approval"]
    # the model's text is kept as text only; it never becomes a command
    assert all("docker" not in o["command"] for o in res["order"])


@pytest.mark.parametrize("order", [
    [{"id": "dependency_failure@influxDB", "rationale": "x"}],                                         # missing one
    [{"id": i, "rationale": "x"} for i in IDS] + [{"id": "network_partition@fms-server", "rationale": "x"}],  # invented
    [{"id": IDS[0], "rationale": "x"}, {"id": IDS[0], "rationale": "y"}],                              # duplicate
    [{"id": "dependency_failure@grafana", "rationale": "x"}, {"id": IDS[1], "rationale": "y"}],        # wrong target
    "dependency_failure first",                                                                        # not a list
])
def test_invalid_plans_fall_back_to_the_rule_based_order(order):
    res = ai_plan(ENTRIES, FINDINGS, _provider({"summary": "s", "order": order}))
    assert res["provider"] == "rule-based" and res["fallback_reason"]
    assert [o["id"] for o in res["order"]] == [f"{e['experiment_type']}@{e['target']}" for e in ENTRIES]


@pytest.mark.parametrize("kwargs, reason", [
    ({"exc": httpx.ConnectError("refused")}, "unreachable"),
    ({"exc": httpx.ReadTimeout("slow")}, "unreachable"),
    ({"status": 404, "answer": {}}, "HTTP 404"),
    ({"content": "Sure! Here is the plan: ..."}, "non-JSON"),
])
def test_unreachable_or_broken_ollama_falls_back(kwargs, reason):
    res = ai_plan(ENTRIES, FINDINGS, _provider(**kwargs))
    assert res["provider"] == "rule-based" and reason in res["fallback_reason"]
    an = ai_analyse(FINDINGS, _provider(**kwargs))
    assert an["provider"] == "rule-based" and an["analyses"] == rule_based_analysis(FINDINGS)


def test_offline_is_deterministic():
    assert ai_plan(ENTRIES, FINDINGS, None) == ai_plan(ENTRIES, FINDINGS, None)
    assert ai_plan(ENTRIES, FINDINGS, None)["fallback_reason"] == "AI disabled"


# ------------------------------------------------------------------------------------------------------------ analyst
def _item(fid, runs, **kw):
    return {"finding": fid, "root_cause": "fms-server keeps serving the last stored status", "confidence": "medium", "evidence_runs": runs,
            "playbook": [{"kind": "verify", "step": "check logs"}, {"kind": "fix", "step": "patch client"}], **kw}


def test_analyst_accepts_valid_items_and_backfills_the_rest_from_rules():
    ans = {"analyses": [
        _item(STALL_SILENT, ["20261010T180000Z-a"]),                       # valid
        _item(DEP_SLOW, ["20261010T180000Z-a"]),                           # cites a run not in that finding -> rejected
        _item(DEP_LOSS, ["20261010T180001Z-b"], confidence="certain"),     # bad confidence -> rejected
        _item("F9", ["20261010T180000Z-a"]),                               # unknown finding -> ignored
    ]}
    res = ai_analyse(FINDINGS, _provider(ans))
    assert res["provider"] == "ollama:qwen2.5:7b"
    assert res["ai_items_accepted"] == 1 and res["ai_items_rejected_or_missing"] == len(FINDINGS) - 1
    by_id = {a["finding"]: a for a in res["analyses"]}
    assert by_id[STALL_SILENT]["source"] == "ollama:qwen2.5:7b" and by_id[STALL_SILENT]["playbook"][0] == {
        "rank": 1, "kind": "verify", "step": "check logs"}
    assert by_id[DEP_SLOW]["source"].startswith("rule-based") and by_id[DEP_LOSS]["source"].startswith("rule-based")
    assert set(by_id) == set(FID.values())


@pytest.mark.parametrize("bad", [
    {"playbook": []}, {"playbook": [{"kind": "execute", "step": "docker restart fms-consumer"}]},
    {"root_cause": "  "}, {"evidence_runs": []}, {"evidence_runs": "20261010T180000Z-a"},
])
def test_malformed_analyst_items_are_rejected(bad):
    res = ai_analyse(FINDINGS, _provider({"analyses": [_item(STALL_SILENT, ["20261010T180000Z-a"], **bad)]}))
    assert res["ai_items_accepted"] == 0
    assert all(a["source"].startswith("rule-based") for a in res["analyses"])


def test_long_ai_text_is_truncated():
    res = ai_analyse(FINDINGS, _provider({"analyses": [_item(STALL_SILENT, ["20261010T180000Z-a"], root_cause="fms-server " + "x " * 2000)]}))
    item = next(a for a in res["analyses"] if a["finding"] == STALL_SILENT)
    assert item["source"] == "ollama:qwen2.5:7b" and len(item["root_cause"]) <= ai.MAX_TEXT


def test_rule_based_analysis_covers_every_finding_with_labelled_causes():
    runs = RUNS + [_rec(SILENT_AFTER, run_id="20261010T180002Z-c", ttr=None, state="LATCHED_UNSAFE")]
    runs[-1]["request"].update(experiment_type="dependency_failure", target="influxDB")
    crash = _rec(STALL, run_id="20261010T180003Z-d")
    crash["request"]["experiment_type"] = "service_crash"
    fs = derive_findings(runs + [crash])
    out = rule_based_analysis(fs)
    assert [a["finding"] for a in out] == [f"F{i}" for i in range(1, len(fs) + 1)]
    assert all(a["playbook"] and a["evidence_runs"] for a in out)
    dns = [a for a in out if a["rule"] in ("not_recovered", "silent_after_rollback", "slow_recovery")]
    assert dns and all("hypothesis" in a["root_cause"] for a in dns)      # unverified mechanism stays labelled
    sc = next(a for a in out if a["experiment_type"] == "service_crash")
    assert any("SIGTERM" in s["step"] for s in sc["playbook"])


def test_markdown_marks_provider_and_advisory():
    md = render_markdown(ai_plan(ENTRIES, FINDINGS, None), ai_analyse(FINDINGS, None))
    assert "Advisory only" in md and "provider: `rule-based`" in md and "Fallback reason: AI disabled" in md
    assert md.count("### F") == len(FINDINGS)


def test_cli_advise_offline_never_contacts_ollama(tmp_path, monkeypatch, capsys):
    import resiliencepilot.__main__ as cli
    monkeypatch.setattr(cli, "build", lambda *_: (_ for _ in ()).throw(AssertionError("safety objects built")))
    monkeypatch.setattr(ai, "OllamaProvider", lambda *_: (_ for _ in ()).throw(AssertionError("AI contacted")))
    for r in RUNS:
        (tmp_path / f"{r['run_id']}.json").write_text(json.dumps(r), encoding="utf-8")
    out = tmp_path / "ai.md"
    assert cli.main(["advise", "--dir", str(tmp_path), "--offline", "--json", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    result = json.loads(printed[:printed.rindex("}") + 1])
    assert result["plan"]["provider"] == "rule-based" and result["analysis"]["provider"] == "rule-based"
    assert out.read_text(encoding="utf-8").startswith("# AI advisory report")


# ---------------------------------------------------------------------------------------------------------- grounding
# The four wrong answers qwen2.5:7b gave in the first live run (2026-10-11) must all be rejected.
@pytest.mark.parametrize("rule, etype, text, notes, why", [
    ("silent_during_fault", "telemetry_stall",
     "The fms-server is not recovering properly, possibly due to a delay in DNS resolution or HTTP client caching.",
     [], "rules out"),
    ("data_loss", "dependency_failure",
     "There is data loss during the fault, possibly due to the fms-forwarder not handling the pause gracefully.",
     [], "does not name"),
    ("silent_after_rollback", "dependency_failure",
     "The fms-server is not recovering properly, possibly due to stale data or delayed recovery.", [], "does not name"),
    ("blast_radius", "service_crash",
     "The service crash has a blast radius affecting other services due to improper dependencies.",
     ["deliberate guard test: operator ran `docker restart grafana`"], "annotation"),
])
def test_grounding_rejects_the_first_live_run_mistakes(rule, etype, text, notes, why):
    assert why in grounding_problem(rule, etype, text, notes)


@pytest.mark.parametrize("rule, etype, text, notes", [
    ("silent_during_fault", "service_crash", "fms-server returns the last stored status with a fresh receivedDateTime", []),
    ("data_loss", "dependency_failure", "fms-consumer drops statuses it cannot write; no buffering", []),
    ("slow_recovery", "dependency_failure", "fms-server kept failing to resolve the host; DNS caching is a hypothesis", []),
    ("blast_radius", "service_crash", "Grafana restart was a deliberate operator test, not a cascade", ["guard test"]),
])
def test_grounding_accepts_evidence_consistent_causes(rule, etype, text, notes):
    assert grounding_problem(rule, etype, text, notes) is None


def test_unhedged_dns_caching_claim_is_rejected():
    assert "unverified hypothesis" in grounding_problem(
        "slow_recovery", "dependency_failure", "fms-server caches failed DNS lookups.", [])


def test_rejection_reason_is_recorded_and_shown():
    ans = {"analyses": [_item(DEP_LOSS, ["20261010T180001Z-b"], root_cause="the fms-forwarder does not buffer")]}
    res = ai_analyse(FINDINGS, _provider(ans))
    item = next(a for a in res["analyses"] if a["finding"] == DEP_LOSS)
    assert item["source"] == "rule-based" and "consumer" in item["ai_rejected"]
    assert {"finding": DEP_LOSS, "reason": item["ai_rejected"],
            "ai_root_cause": "the fms-forwarder does not buffer"} in res["rejected"]      # model's text kept for review
    assert "_AI answer rejected:" in render_markdown(ai_plan(ENTRIES, FINDINGS, None), res)


def test_prompt_gives_each_finding_only_its_own_facts_and_notes():
    fs = derive_findings(RUNS)
    fs[0]["runs"][0]["note"] = "deliberate test"
    body = json.loads(ai.analysis_prompt(fs))
    first, loss = body["findings"][0], next(f for f in body["findings"] if f["rule"] == "data_loss")
    assert first["runs"][0]["operator_note"] == "deliberate test"
    assert any("drops statuses" in x for x in loss["facts"]) and not any("SIGTERM" in x for x in loss["facts"])
    assert "facts" not in body                       # no global fact dump
