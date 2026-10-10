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

    def pause(self, name: str) -> None:
        proc = self._run("pause", _check_name(name))
        if proc.returncode != 0:
            raise DockerError(f"docker pause {name}: {proc.stderr.strip()}")

    def unpause(self, name: str) -> None:
        proc = self._run("unpause", _check_name(name))
        if proc.returncode != 0:
            raise DockerError(f"docker unpause {name}: {proc.stderr.strip()}")
