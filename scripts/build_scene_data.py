#!/usr/bin/env python3
"""Build the embedded data for docs/scene/index.html (3D visualization of RECORDED runs).

Read-only: reads committed files only and rewrites the JSON block between the SCENE-DATA markers in
docs/scene/index.html. Nothing here talks to Docker, the API or the network.

Sources (every value in the page is taken from one of these, with its path and, where useful, its line):
  reports/{day1,day2,guardrail-demo}/<run_id>.json   run records
  reports/day2/scores.json, reports/day2/findings.md, reports/day2/annotations.json
  reports/day2/ai_calls/*.json                        AI call logs (committed)
  config/dependency_map.json, config/safety.json, config/experiments.json, config/ai.json
  Fleet Management/fleet-management/fms-blueprint-compose*.yaml, config/compose/resiliencepilot.override.yaml

Usage: python3 scripts/build_scene_data.py [--check]
  --check  does not write; exit 1 if the embedded data is out of date or the offline check fails
           (http(s) resource loads, fetch/XMLHttpRequest/WebSocket/... in the page code).
"""
from __future__ import annotations

import ast
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "docs", "scene", "index.html")
BEGIN = '<script id="scene-data" type="application/json">'
END = "</script><!-- /SCENE-DATA -->"
RUN_DIRS = ["reports/day1", "reports/day2", "reports/guardrail-demo"]
BLUEPRINT = "Fleet Management/fleet-management"
COMPOSE_FILES = [f"{BLUEPRINT}/fms-blueprint-compose.yaml", f"{BLUEPRINT}/fms-blueprint-compose-zenoh.yaml",
                 "config/compose/resiliencepilot.override.yaml"]


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def read(relpath: str) -> str:
    with open(os.path.join(ROOT, relpath), encoding="utf-8") as f:
        return f.read()


def load(relpath: str):
    return json.loads(read(relpath))


def line_of(relpath: str, needle: str, start: int = 1) -> int | None:
    """1-based line number of the first line at or after `start` containing `needle`."""
    for i, line in enumerate(read(relpath).splitlines(), 1):
        if i >= start and needle in line:
            return i
    return None


def src(relpath: str, line: int | None = None) -> str:
    return f"{relpath}:{line}" if line else relpath


# ---------------------------------------------------------------- compose (image, ports, container name)

def compose_services() -> dict:
    """Minimal line parser for the three compose files (no PyYAML dependency). Later files override earlier ones."""
    out: dict[str, dict] = {}
    for path in COMPOSE_FILES:
        lines = read(path).splitlines()
        in_services = False
        svc = None
        key = None
        for i, line in enumerate(lines, 1):
            if re.match(r"^services:\s*$", line):
                in_services = True
                continue
            if re.match(r"^\S", line):
                in_services = False
                svc = None
                continue
            if not in_services:
                continue
            m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
            if m:
                svc = out.setdefault(m.group(1), {"service": m.group(1)})
                key = None
                continue
            if svc is None:
                continue
            m = re.match(r"^    ([a-z_]+):\s*(.*)$", line)
            if m:
                key, val = m.group(1), m.group(2).strip()
                if key == "image":
                    svc["image"] = val.strip('"')
                    svc["image_src"] = src(path, i)
                elif key == "container_name":
                    svc["container_name"] = val.strip('"')
                    svc["container_src"] = src(path, i)
                elif key == "platform":
                    svc["platform"] = val
                    svc["platform_src"] = src(path, i)
                elif key == "ports":
                    svc["ports"] = []
                    svc["ports_src"] = src(path, i)
                continue
            m = re.match(r"^      - (.+)$", line)
            if m and key == "ports":
                svc["ports"].append(m.group(1).strip().strip('"'))
    by_container = {}
    for s in out.values():
        if "container_name" in s:
            by_container[s["container_name"]] = s
    return by_container


# ---------------------------------------------------------------- config

def dependency_map() -> dict:
    path = "config/dependency_map.json"
    dm = load(path)
    for e in dm["edges"]:
        e["src"] = src(path, line_of(path, f'{{"from": "{e["from"]}", "to": "{e["to"]}"'))
    nodes = {}
    for name, n in dm["nodes"].items():
        nodes[name] = dict(n, src=src(path, line_of(path, f'"{name}":')))
    return {"about": dm["_about"], "about_src": src(path, line_of(path, '"_about"')),
            "telemetry_path": dm["telemetry_path"], "telemetry_path_src": src(path, line_of(path, '"telemetry_path"')),
            "nodes": nodes, "edges": dm["edges"],
            "timestamps": dm["timestamps"], "timestamps_src": src(path, line_of(path, '"timestamps"')),
            "not_present": dm["not_present"], "path": path}


def keyed_lines(path: str, obj: dict, start: int = 1) -> dict:
    out = {}
    for k, v in obj.items():
        ln = line_of(path, f'"{k}":', start)
        out[k] = {"value": v, "src": src(path, ln)}
    return out


def safety() -> dict:
    path = "config/safety.json"
    s = load(path)
    flat = keyed_lines(path, {k: v for k, v in s.items() if k != "guardrails"})
    g_start = line_of(path, '"guardrails"') or 1
    flat_g = keyed_lines(path, s.get("guardrails", {}), g_start)
    return {"top": flat, "guardrails": flat_g, "path": path}


def experiments() -> dict:
    path = "config/experiments.json"
    ex = load(path)
    out = {}
    for name, spec in ex.items():
        start = line_of(path, f'"{name}": {{') or 1
        out[name] = {"fields": keyed_lines(path, spec, start), "src": src(path, start)}
    return out


def ai_config() -> dict:
    path = "config/ai.json"
    return {"fields": keyed_lines(path, load(path)), "path": path}


# ---------------------------------------------------------------- reports

def parse_findings() -> dict:
    path = "reports/day2/findings.md"
    lines = read(path).splitlines()
    findings = []
    cur = None
    for i, line in enumerate(lines, 1):
        m = re.match(r"^### (F\d+)\. (.+?) \((high|medium|low)\)\s*$", line)
        if m:
            cur = {"id": m.group(1), "title": m.group(2), "level": m.group(3), "src": src(path, i), "items": []}
            findings.append(cur)
            continue
        if line.startswith("## "):
            cur = None
        if cur and line.startswith("- `"):
            m = re.match(r"^- `([0-9TZ]+-[0-9a-f]+)`: (.*)$", line)
            if m:
                cur["items"].append({"run_id": m.group(1), "text": m.group(2), "src": src(path, i)})
    head = next((l for l in lines if l.startswith("Generated by")), "")
    return {"path": path, "header": head, "findings": findings}


def ai_calls() -> list:
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "reports", "*", "ai_calls", "*.json"))):
        p = rel(path)
        log = json.load(open(path, encoding="utf-8"))
        calls = []
        for idx, c in enumerate(log.get("calls", [])):
            schema = c.get("schema")
            schema_s = schema if isinstance(schema, str) else json.dumps(schema)
            role = ("Planner agent (AI)" if "'order'" in schema_s or '"order"' in schema_s
                    else "Analyst agent (AI)" if "analyses" in schema_s else "not recorded")
            v = c.get("validation")
            parsed = None
            if isinstance(v, str) and v.startswith("{"):
                try:
                    parsed = ast.literal_eval(v)
                except (ValueError, SyntaxError):
                    parsed = None
            elif isinstance(v, dict):
                parsed = v
            entry = {"index": idx, "role": role, "provider": c.get("provider"), "model": c.get("model"),
                     "options": c.get("options"), "http_status": c.get("http_status"), "error": c.get("error"),
                     "validation_text": v if isinstance(v, str) and parsed is None else None}
            if parsed is not None:
                entry["accepted"] = parsed.get("accepted", [])
                entry["rejected"] = parsed.get("rejected", [])
                entry["missing"] = parsed.get("missing", [])
            calls.append(entry)
        out.append({"path": p, "logged_at": log.get("logged_at"), "command": log.get("command"),
                    "runs_dir": log.get("runs_dir"), "n_runs": log.get("n_runs"), "calls": calls})
    return out


def scores() -> dict:
    path = "reports/day2/scores.json"
    s = load(path)
    by_run = {}
    for r in s.get("runs", []):
        by_run[r["run_id"]] = {"score": r.get("score"), "scorable": r.get("scorable"), "partial": r.get("partial"),
                               "components": r.get("components"), "silent": r.get("silent"),
                               "not_measured": r.get("not_measured"), "reason": r.get("reason"),
                               "src": src(path, line_of(path, f'"run_id": "{r["run_id"]}"'))}
    return {"path": path, "formula_src": s.get("formula"), "by_run": by_run, "summary": s.get("summary")}


def group_of(run_dir: str, run: dict, annotation: str | None) -> str:
    if run_dir.endswith("guardrail-demo"):
        return "guardrail-demo runs"
    if annotation and "kill switch" in annotation:
        return "kill-switch run"
    return run["request"]["experiment_type"]


def slim_obs(o: dict) -> dict:
    ob = o.get("observation") or {}
    a = o.get("assessment") or {}
    return {"phase": o.get("phase"), "t_s": o.get("t_s"),
            "observed_at": ob.get("observed_at"), "http_status": ob.get("http_status"),
            "latency_ms": ob.get("latency_ms"), "vin": ob.get("vin"), "created_at": ob.get("created_at"),
            "received_at": ob.get("received_at"), "server_time": ob.get("server_time"), "error": ob.get("error"),
            "data_age_s": ob.get("data_age_s"),
            "freshness": a.get("freshness"), "stalled_for_s": a.get("stalled_for_s"), "advanced": a.get("advanced"),
            "healthy_but_stale": a.get("healthy_but_stale")}


def runs(annotations: dict, findings: dict, score_map: dict) -> list:
    find_by_run: dict[str, list] = {}
    for f in findings["findings"]:
        for it in f["items"]:
            find_by_run.setdefault(it["run_id"], []).append(
                {"id": f["id"], "title": f["title"], "level": f["level"], "text": it["text"], "src": it["src"]})
    out = []
    for d in RUN_DIRS:
        for path in sorted(glob.glob(os.path.join(ROOT, d, "2026*-*.json"))):
            r = json.load(open(path, encoding="utf-8"))
            if "run_id" not in r:
                continue
            rid = r["run_id"]
            ann = annotations.get(rid)
            out.append({
                "run_id": rid, "src": rel(path), "dir": d, "group": group_of(d, r, ann),
                "request": r.get("request"), "state": r.get("state"), "stop_reason": r.get("stop_reason"),
                "config_profile": r.get("config_profile"), "platform": r.get("platform"),
                "spec": r.get("spec"), "limits": r.get("limits"), "timeline": r.get("timeline"),
                "observations": [slim_obs(o) for o in r.get("observations", [])],
                "observation_fields": sorted({k for o in r.get("observations", []) for k in (o.get("observation") or {})}),
                "rollback": r.get("rollback"), "recovery": r.get("recovery"), "impact": r.get("impact"),
                "guardrails": r.get("guardrails"), "data_gap": r.get("data_gap"),
                "evidence_errors": r.get("evidence_errors"),
                "annotation": ann, "annotation_src": "reports/day2/annotations.json" if ann else None,
                "score": score_map.get(rid), "findings": find_by_run.get(rid, []),
            })
    return out


def build() -> dict:
    ann_path = "reports/day2/annotations.json"
    annotations = {k: v for k, v in load(ann_path).items() if not k.startswith("_")}
    findings = parse_findings()
    sc = scores()
    return {
        "_about": "Generated by scripts/build_scene_data.py from committed files only. Do not edit by hand.",
        "explainer_href": "../../reports/day1/explainer.html",
        "compose_project": load("config/safety.json").get("compose_project"),
        "compose": compose_services(),
        "compose_files": COMPOSE_FILES,
        "dependency_map": dependency_map(),
        "safety": safety(),
        "experiments": experiments(),
        "ai_config": ai_config(),
        "ai_calls": ai_calls(),
        "ai_layer_doc": {"path": "docs/AI_LAYER.md",
                         "boundary_src": src("docs/AI_LAYER.md", line_of("docs/AI_LAYER.md", "| The AI gets")),
                         "gets": ["Rule-based plan entries (ids, evidence counts, rationale)",
                                  "Findings (rule, run ids, measured details, operator notes)",
                                  "A curated, labelled fact list from `reports/day2/README.md`"],
                         "never": ["Shell, Docker socket, `docker_cli`, fault adapters",
                                   "Safety controller, lock, latch or reset",
                                   "Credentials, environment variables, raw inspect data"],
                         "quotes": [{"text": q, "src": src("docs/AI_LAYER.md", line_of("docs/AI_LAYER.md", q))} for q in [
                             "The planner can only **reorder** the rule-based candidates.",
                             "Output is text for a human. Nothing in it is executed.",
                             "`ai.py` imports only `json`, `dataclasses`, `pathlib`, `urllib.parse` and `httpx`",
                             "The Ollama URL must be loopback"]]},
        "findings": findings,
        "scores": {"path": sc["path"], "summary": sc["summary"]},
        "runs": runs(annotations, findings, sc["by_run"]),
    }


def verify_ai_boundary_text(data: dict) -> None:
    """The boundary rows above are copied from docs/AI_LAYER.md; fail loudly if the doc changed."""
    doc = read("docs/AI_LAYER.md")
    for s in data["ai_layer_doc"]["gets"] + data["ai_layer_doc"]["never"] + [q["text"] for q in data["ai_layer_doc"]["quotes"]]:
        if s not in doc:
            raise SystemExit(f"docs/AI_LAYER.md no longer contains: {s!r}; update build_scene_data.py")


NET_APIS = ["fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "importScripts", "import("]


def offline_check(page: str) -> list[str]:
    """Static check that the page loads nothing over the network. Returns a list of problems (empty = pass)."""
    problems = []
    a = page.index(BEGIN)
    b = page.index(END, a) + len(END)
    code = page[:a] + page[b:]          # page markup + scripts, without the embedded JSON data block
    for m in re.finditer(r"""(?:src|href|action|data|poster)\s*=\s*["']([^"']*)["']""", code, re.I):
        url = m.group(1).strip()
        if re.match(r"^(https?:)?//", url, re.I):
            problems.append(f"index.html: network URL in attribute: {url}")
    for m in re.finditer(r"""url\(\s*["']?([^"')]*)""", code, re.I):
        if re.match(r"^(https?:)?//", m.group(1).strip(), re.I):
            problems.append(f"index.html: network URL in CSS url(): {m.group(1)}")
    if re.search(r"@import", code, re.I):
        problems.append("index.html: CSS @import")
    for api in NET_APIS:
        if api in code:
            problems.append(f"index.html: network API in page code: {api}")
    if re.search(r"https?://", code, re.I):
        problems.append("index.html: http(s):// string in page code outside the data block")
    scripts = re.findall(r"<script[^>]*\ssrc=[\"']([^\"']+)", code, re.I)
    if scripts != ["three.r160.min.js"]:
        problems.append(f"index.html: unexpected external scripts: {scripts}")
    if re.search(r"<(link|iframe|img|object|embed|audio|video|source)\b", code, re.I):
        problems.append("index.html: element that can load a resource")
    if not os.path.exists(os.path.join(os.path.dirname(PAGE), "three.r160.min.js")):
        problems.append("three.r160.min.js missing")
    return problems


def main() -> int:
    with open(PAGE, encoding="utf-8") as f:
        page = f.read()
    try:
        data = build()
    except FileNotFoundError as e:
        print(f"cannot rebuild the embedded data: missing {rel(e.filename)}. On a fresh clone run "
              "`git submodule update --init` first (the page itself does not need it).")
        if "--check" not in sys.argv:
            return 2
        data = None
    if data is not None:
        verify_ai_boundary_text(data)
        blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        a = page.index(BEGIN) + len(BEGIN)
        b = page.index(END, a)
        new = page[:a] + blob + page[b:]
    if "--check" in sys.argv:
        rc = 0
        if data is None:
            print("docs/scene/index.html: embedded data NOT verified (sources missing, see above)")
            rc = 2
        elif new != page:
            print("docs/scene/index.html: embedded data is out of date; run scripts/build_scene_data.py")
            rc = 1
        else:
            print("docs/scene/index.html: embedded data up to date")
        problems = offline_check(page)
        for p in problems:
            print("OFFLINE CHECK FAIL:", p)
        if not problems:
            print("offline check: PASS (index.html: no http(s) resource loads, no fetch/XMLHttpRequest/WebSocket/"
                  "EventSource/sendBeacon/import(); only script is ./three.r160.min.js)")
        lib = open(os.path.join(os.path.dirname(PAGE), "three.r160.min.js"), encoding="utf-8").read()
        hits = {api: lib.count(api) for api in ("fetch(", "XMLHttpRequest", "http://", "https://") if api in lib}
        print(f"note: vendored three.r160.min.js contains {hits} (library loader code and the upstream deprecation "
              "message; the page never calls a three.js loader)")
        urls = sorted(set(re.findall(r"https?://[^\s\"'<>]+", page[page.index(BEGIN):page.index(END)])))
        print(f"note: URLs appearing as text values in the embedded data (not loaded): {urls}")
        return rc or (1 if problems else 0)
    with open(PAGE, "w", encoding="utf-8") as f:
        f.write(new)
    print(f"wrote {rel(PAGE)}: {len(data['runs'])} runs, {len(blob):,} bytes of data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
