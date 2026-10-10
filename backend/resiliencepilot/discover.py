"""Semi-automatic dependency discovery (read-only).

Builds a candidate dependency graph of the sandbox compose project from what Docker reports, then diffs it against the
curated `config/dependency_map.json`. Evidence sources, strongest first:

  env_reference     : an environment variable of X mentions a host name of Y (e.g. KUKSA_DATABROKER_URI=http://databroker:55556)
  config_reference  : a text file bind-mounted into X mentions a host name of Y (e.g. zenoh "tcp/fms-zenoh-router:7447")
  command_reference : X's command line mentions a host name of Y
  depends_on        : compose start-order dependency label (com.docker.compose.depends_on)

An edge X -> Y means "X connects to / needs Y". Nothing is executed inside containers; secrets are never stored
(values of TOKEN/PASSWORD/SECRET/KEY variables are redacted and only the matched host reference is kept).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
DEPENDS_LABEL = "com.docker.compose.depends_on"
_SECRET_KEY = re.compile(r"(TOKEN|PASSWORD|PASSWD|SECRET|KEY|CREDENTIAL)", re.IGNORECASE)
_MAX_FILE_BYTES = 256 * 1024
STRENGTH = {"env_reference": 3, "config_reference": 3, "command_reference": 2, "depends_on": 1}


@dataclass
class Node:
    name: str
    service: str | None
    image: str
    networks: list[str]
    aliases: set[str]
    ports: list[str]
    healthcheck: bool
    restart_policy: str | None


@dataclass
class Edge:
    source: str
    target: str
    evidence: list[dict] = field(default_factory=list)

    @property
    def kinds(self) -> list[str]:
        return sorted({e["kind"] for e in self.evidence}, key=lambda k: -STRENGTH[k])

    @property
    def confidence(self) -> str:
        best = max(STRENGTH[k] for k in self.kinds)
        return "high" if best >= 3 else "medium" if best == 2 else "low"


def _host_path(source: str) -> Path | None:
    """Map a Docker Desktop bind-mount source back to a host path (best effort)."""
    m = re.match(r"^/run/desktop/mnt/host/([a-z])/(.*)$", source)
    if m:
        return Path(f"{m.group(1).upper()}:/{m.group(2)}")
    p = Path(source)
    return p if p.is_absolute() else None


def node_from_inspect(info: dict) -> Node:
    labels = info.get("Config", {}).get("Labels") or {}
    nets = info.get("NetworkSettings", {}).get("Networks") or {}
    name = info["Name"].lstrip("/")
    aliases = {name, name.lower()}
    if labels.get(SERVICE_LABEL):
        aliases.add(labels[SERVICE_LABEL])
    for n in nets.values():
        for a in (n.get("Aliases") or []) + (n.get("DNSNames") or []):
            if a and not re.fullmatch(r"[0-9a-f]{12,64}", a):   # container-id aliases are not meaningful names
                aliases.add(a)
    ports = sorted({f"{b.get('HostIp', '')}:{b.get('HostPort')}->{p}"
                    for p, binds in (info.get("NetworkSettings", {}).get("Ports") or {}).items() for b in (binds or [])})
    return Node(name=name, service=labels.get(SERVICE_LABEL), image=info.get("Config", {}).get("Image", ""),
                networks=sorted(nets), aliases=aliases, ports=ports,
                healthcheck=bool(info.get("Config", {}).get("Healthcheck")),
                restart_policy=(info.get("HostConfig", {}).get("RestartPolicy") or {}).get("Name"))


def _mentions(text: str, alias: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(alias)}(?![A-Za-z0-9_-])", text) is not None


def _snippet(text: str, alias: str) -> str:
    m = re.search(rf"[^\s\"',]*{re.escape(alias)}[^\s\"',]*", text)
    return m.group(0)[:120] if m else alias


def discover(infos: list[dict], read_file=None) -> tuple[list[Node], list[Edge]]:
    """Pure: derive nodes and candidate edges from `docker inspect` dicts. `read_file(path) -> str|None` is injected."""
    nodes = [node_from_inspect(i) for i in infos]
    by_name = {n.name: n for n in nodes}
    service_to_name = {n.service: n.name for n in nodes if n.service}
    edges: dict[tuple[str, str], Edge] = {}

    def add(src: str, dst: str, ev: dict) -> None:
        if src == dst:
            return
        edges.setdefault((src, dst), Edge(src, dst)).evidence.append(ev)

    for info in infos:
        src = info["Name"].lstrip("/")
        others = [n for n in nodes if n.name != src]
        # 1) environment variables (values of secret-looking keys are never inspected)
        for kv in info.get("Config", {}).get("Env") or []:
            key, _, value = kv.partition("=")
            if _SECRET_KEY.search(key):
                continue
            for o in others:
                hit = next((a for a in sorted(o.aliases, key=len, reverse=True) if _mentions(value, a)), None)
                if hit:
                    add(src, o.name, {"kind": "env_reference", "detail": f"{key}={_snippet(value, hit)}"})
        # 2) command line
        cmd = " ".join((info.get("Path") or "",) + tuple(info.get("Args") or ()))
        for o in others:
            hit = next((a for a in sorted(o.aliases, key=len, reverse=True) if _mentions(cmd, a)), None)
            if hit:
                add(src, o.name, {"kind": "command_reference", "detail": _snippet(cmd, hit)})
        # 3) bind-mounted text files
        if read_file is not None:
            for m in info.get("Mounts") or []:
                if m.get("Type") != "bind":
                    continue
                text = read_file(m.get("Source", ""))
                if not text:
                    continue
                for o in others:
                    hit = next((a for a in sorted(o.aliases, key=len, reverse=True) if _mentions(text, a)), None)
                    if hit:
                        add(src, o.name, {"kind": "config_reference",
                                          "detail": f"{m.get('Destination')}: {_snippet(text, hit)}"})
        # 4) compose depends_on label: "svc:condition:restart,svc2:..."
        dep = (info.get("Config", {}).get("Labels") or {}).get(DEPENDS_LABEL) or ""
        for item in filter(None, dep.split(",")):
            svc, _, cond = item.partition(":")
            if svc in service_to_name:
                add(src, service_to_name[svc], {"kind": "depends_on", "detail": f"{svc}:{cond}"})
    for e in edges.values():  # stable order of evidence
        e.evidence.sort(key=lambda ev: -STRENGTH[ev["kind"]])
    return nodes, sorted(edges.values(), key=lambda e: (e.source, e.target))


def read_text_file(source: str) -> str | None:
    p = _host_path(source)
    try:
        if p is None or not p.is_file() or p.stat().st_size > _MAX_FILE_BYTES:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def diff_against_map(edges: list[Edge], dep_map: dict) -> dict:
    """Compare discovered edges with the curated map. Matching is by unordered pair; direction is reported."""
    curated = [(e["from"], e["to"]) for e in dep_map["edges"]]
    host_only = {n for n, v in dep_map["nodes"].items() if v.get("side") == "host"}
    disc = {(e.source, e.target): e for e in edges}
    matched, direction_differs, map_only = [], [], []
    seen = set()
    for a, b in curated:
        if a in host_only or b in host_only:
            continue                              # not a container: cannot be discovered from Docker
        if (a, b) in disc:
            matched.append({"from": a, "to": b, "evidence": disc[(a, b)].kinds})
            seen.add((a, b))
        elif (b, a) in disc:
            direction_differs.append({"map": f"{a} -> {b}", "discovered": f"{b} -> {a}",
                                      "evidence": disc[(b, a)].kinds,
                                      "note": "map follows data flow; discovery follows who connects to whom"})
            seen.add((b, a))
        else:
            map_only.append({"from": a, "to": b})
    discovered_only = [{"from": s, "to": t, "confidence": e.confidence, "evidence": e.evidence}
                       for (s, t), e in disc.items() if (s, t) not in seen]
    return {"matched": matched, "direction_differs": direction_differs, "map_only": map_only,
            "discovered_only": discovered_only,
            "not_discoverable": sorted(host_only)}


def to_json(nodes: list[Node], edges: list[Edge], diff: dict) -> dict:
    return {
        "nodes": [{"name": n.name, "service": n.service, "image": n.image, "networks": n.networks, "ports": n.ports,
                   "healthcheck": n.healthcheck, "restart_policy": n.restart_policy} for n in nodes],
        "edges": [{"from": e.source, "to": e.target, "confidence": e.confidence, "evidence": e.evidence} for e in edges],
        "diff_vs_dependency_map": diff,
    }


def load_map(root: Path) -> dict:
    return json.loads((root / "config" / "dependency_map.json").read_text(encoding="utf-8"))
