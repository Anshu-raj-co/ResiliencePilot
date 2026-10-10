"""The only module that executes Docker commands. Arguments are passed as a list (no shell)."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Protocol

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
PROJECT_LABEL = "com.docker.compose.project"


class DockerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContainerState:
    name: str
    exists: bool
    running: bool = False
    paused: bool = False
    status: str = "missing"
    project: str | None = None


class Docker(Protocol):
    def inspect(self, name: str) -> ContainerState: ...
    def pause(self, name: str) -> None: ...
    def unpause(self, name: str) -> None: ...


def _check_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise DockerError(f"refusing invalid container name {name!r}")
    return name


class DockerCLI:
    def __init__(self, timeout_s: float = 15.0) -> None:
        self.timeout_s = timeout_s

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=self.timeout_s, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DockerError(f"docker {args[0]} failed: {exc}") from exc

    def inspect(self, name: str) -> ContainerState:
        proc = self._run("inspect", "--type", "container", _check_name(name))
        if proc.returncode != 0:
            if "No such" in proc.stderr:
                return ContainerState(name=name, exists=False)
            raise DockerError(f"docker inspect {name}: {proc.stderr.strip()}")
        info = json.loads(proc.stdout)[0]
        st = info["State"]
        return ContainerState(name=name, exists=True, running=bool(st["Running"]), paused=bool(st["Paused"]),
                              status=st["Status"], project=(info["Config"].get("Labels") or {}).get(PROJECT_LABEL))

    # ---- read-only helpers for evidence/display (never used by fault logic) ----
    def networks(self, name: str) -> list[str]:
        proc = self._run("inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", _check_name(name))
        return proc.stdout.split() if proc.returncode == 0 else []

    def env(self, name: str) -> dict[str, str]:
        proc = self._run("inspect", "-f", "{{json .Config.Env}}", _check_name(name))
        if proc.returncode != 0:
            return {}
        return dict(e.split("=", 1) for e in json.loads(proc.stdout) if "=" in e)

    def stats(self, project: str) -> list[dict]:
        """One `docker stats --no-stream` snapshot of the project's containers."""
        ids = self._run("ps", "-q", "--filter", f"label={PROJECT_LABEL}={project}").stdout.split()
        if not ids:
            return []
        proc = self._run("stats", "--no-stream", "--format", "{{json .}}", *ids)
        rows = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        return [{"name": r.get("Name"), "cpu": r.get("CPUPerc"), "mem": r.get("MemUsage")} for r in rows]

    def mem_total_bytes(self) -> int | None:
        proc = self._run("info", "--format", "{{.MemTotal}}")
        return int(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip().isdigit() else None

    def pause(self, name: str) -> None:
        proc = self._run("pause", _check_name(name))
        if proc.returncode != 0:
            raise DockerError(f"docker pause {name}: {proc.stderr.strip()}")

    def unpause(self, name: str) -> None:
        proc = self._run("unpause", _check_name(name))
        if proc.returncode != 0:
            raise DockerError(f"docker unpause {name}: {proc.stderr.strip()}")
