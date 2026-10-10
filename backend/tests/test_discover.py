"""Unit tests for read-only dependency discovery (discover.py). Inspect records are trimmed copies of the live stack's."""
from __future__ import annotations

import pytest

from resiliencepilot.discover import _host_path, diff_against_map, discover, node_from_inspect, read_text_file, to_json
from resiliencepilot.docker_cli import DockerCLI, DockerError

PROJECT = "resiliencepilot-sdv"


def _info(name, service=None, env=(), args=(), mounts=(), depends="", aliases=None, dns=None, health=False, ports=None):
    service = service or name
    labels = {"com.docker.compose.project": PROJECT, "com.docker.compose.service": service}
    if depends:
        labels["com.docker.compose.depends_on"] = depends
    net = {"Aliases": aliases if aliases is not None else [service, service],
           "DNSNames": dns if dns is not None else [name, "42bcea84d5a7"]}
    return {"Name": "/" + name, "Path": args[0] if args else "", "Args": list(args[1:]),
            "Config": {"Labels": labels, "Env": list(env), "Image": f"img/{service}",
                       "Healthcheck": {"Test": ["CMD", "true"]} if health else None},
            "HostConfig": {"RestartPolicy": {"Name": "no"}},
            "NetworkSettings": {"Networks": {f"{PROJECT}_fms-backend": net}, "Ports": ports or {}},
            "Mounts": list(mounts)}


def _stack():
    """Five containers wired the way the blueprint wires them (env, zenoh config file, depends_on)."""
    return [
        _info("databroker"),
        _info("csv-provider", env=["KUKSA_DATA_BROKER_ADDR=databroker"], depends="databroker:service_started:false"),
        _info("fms-forwarder", env=["KUKSA_DATABROKER_URI=http://databroker:55556"],
              args=["/app/fms-forwarder", "zenoh", "-c", "/zenoh-config.json5"],
              mounts=[{"Type": "bind", "Source": "/host/config-client.json5", "Destination": "/zenoh-config.json5"}],
              depends="csv-provider:service_started:false,fms-zenoh-router:service_started:false"),
        _info("fms-zenoh-router"),
        _info("influxDB", service="influxdb", health=True,
              ports={"8086/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8086"}]}),
        _info("fms-consumer", env=["INFLUXDB_URI=http://influxdb:8086", "INFLUXDB_TOKEN_FILE=/tmp/fms-demo.token",
                                   "INFLUXDB_TOKEN=secret-influxdb-host-token"],
              mounts=[{"Type": "bind", "Source": "/host/config-client.json5", "Destination": "/zenoh-config.json5"}]),
    ]


_FILES = {"/host/config-client.json5": '{ connect: { endpoints: ["tcp/fms-zenoh-router:7447"] } }'}


def _edges(infos=None, read_file=_FILES.get):
    _, edges = discover(infos or _stack(), read_file)
    return {(e.source, e.target): e for e in edges}


def test_env_reference_matches_service_alias_and_container_name():
    e = _edges()
    assert e[("fms-forwarder", "databroker")].kinds == ["env_reference"]
    assert e[("fms-forwarder", "databroker")].confidence == "high"
    # the compose service name "influxdb" resolves to the container named "influxDB"
    assert "env_reference" in e[("fms-consumer", "influxDB")].kinds
    assert e[("fms-consumer", "influxDB")].evidence[0]["detail"] == "INFLUXDB_URI=http://influxdb:8086"


def test_bind_mounted_config_file_is_evidence():
    e = _edges()
    ev = e[("fms-consumer", "fms-zenoh-router")].evidence
    assert ev == [{"kind": "config_reference", "detail": "/zenoh-config.json5: tcp/fms-zenoh-router:7447"}]


def test_depends_on_alone_is_low_confidence_and_strong_evidence_sorts_first():
    e = _edges()
    assert e[("fms-forwarder", "csv-provider")].confidence == "low"
    # forwarder -> zenoh router has both a config file reference and depends_on; strongest evidence is listed first
    assert e[("fms-forwarder", "fms-zenoh-router")].kinds == ["config_reference", "depends_on"]
    assert e[("fms-forwarder", "fms-zenoh-router")].confidence == "high"


def test_without_file_reader_no_config_edges():
    e = _edges(read_file=None)
    assert ("fms-consumer", "fms-zenoh-router") not in e
    assert e[("fms-forwarder", "fms-zenoh-router")].kinds == ["depends_on"]


def test_command_line_reference_is_medium_confidence():
    infos = [_info("a", args=["/bin/app", "--peer", "tcp/b:7447"]), _info("b")]
    e = _edges(infos)
    assert e[("a", "b")].kinds == ["command_reference"] and e[("a", "b")].confidence == "medium"


def test_secret_variables_are_never_inspected_or_stored():
    infos = [_info("a", env=["DB_PASSWORD=b", "API_KEY=http://b:1", "INFLUX_TOKEN=b"]), _info("b")]
    assert ("a", "b") not in _edges(infos)
    blob = str(to_json(*discover(_stack(), _FILES.get)[:2], {}))
    assert "secret-influxdb-host-token" not in blob


def test_credentials_in_urls_are_redacted_from_evidence():
    infos = [_info("a", env=["UPSTREAM=http://admin:hunter2@b:8086/x"], args=["/app", "--db=postgres://u:pw@b/db"]),
             _info("b")]
    e = _edges(infos)
    details = " ".join(ev["detail"] for ev in e[("a", "b")].evidence)
    assert "hunter2" not in details and "pw@" not in details and "admin" not in details
    assert "http://***@b:8086/x" in details


def test_no_self_edges_and_container_id_aliases_ignored():
    infos = [_info("a", env=["SELF=http://a:1", "X=42bcea84d5a7"]), _info("b")]
    assert _edges(infos) == {}
    node = node_from_inspect(infos[0])
    assert "42bcea84d5a7" not in node.aliases


def test_alias_must_be_a_whole_host_name():
    # "databroker-old" and "mydatabroker" must not count as references to "databroker"
    infos = [_info("a", env=["X=http://databroker-old:1", "Y=mydatabroker"]), _info("databroker")]
    assert _edges(infos) == {}


def test_depends_on_unknown_service_is_ignored():
    infos = [_info("a", depends="not-running:service_started:false"), _info("b")]
    assert _edges(infos) == {}


def test_node_fields():
    node = node_from_inspect(_stack()[4])
    assert node.name == "influxDB" and node.service == "influxdb" and node.healthcheck
    assert node.ports == ["127.0.0.1:8086->8086/tcp"] and node.restart_policy == "no"
    assert {"influxDB", "influxdb"} <= node.aliases


_MAP = {"nodes": {"a": {"side": "vehicle"}, "b": {"side": "backend"}, "c": {"side": "backend"},
                  "d": {"side": "backend"}, "tool": {"side": "host"}},
        "edges": [{"from": "a", "to": "b"}, {"from": "c", "to": "b"}, {"from": "a", "to": "d"},
                  {"from": "tool", "to": "b"}]}


def test_diff_against_map_classifies_every_edge():
    infos = [_info("a", env=["B=http://b:1"]), _info("b", env=["C=c"]), _info("c"),
             _info("e", depends="b:service_started:false")]
    _, edges = discover(infos)
    diff = diff_against_map(edges, _MAP)
    assert diff["matched"] == [{"from": "a", "to": "b", "evidence": ["env_reference"]}]
    assert [d["discovered"] for d in diff["direction_differs"]] == ["b -> c"]
    assert diff["map_only"] == [{"from": "a", "to": "d"}]
    assert [(d["from"], d["to"], d["confidence"]) for d in diff["discovered_only"]] == [("e", "b", "low")]
    assert diff["not_discoverable"] == ["tool"]           # host-side node: never expected from Docker


def test_host_path_mapping():
    assert str(_host_path("/run/desktop/mnt/host/c/Users/x/f.json5")).replace("\\", "/") == "C:/Users/x/f.json5"
    assert str(_host_path("/Users/x/f.json5")) == "/Users/x/f.json5"
    assert _host_path("relative/path") is None


def test_read_text_file_limits(tmp_path):
    small = tmp_path / "a.json5"
    small.write_text("tcp/fms-zenoh-router:7447", encoding="utf-8")
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (256 * 1024 + 1))
    assert read_text_file(str(small)) == "tcp/fms-zenoh-router:7447"
    assert read_text_file(str(big)) is None                 # over the size cap
    assert read_text_file(str(tmp_path)) is None            # a directory, not a file
    assert read_text_file(str(tmp_path / "missing")) is None


def test_inspect_raw_refuses_anything_but_container_ids():
    cli = DockerCLI()
    for bad in ["fms-forwarder", "--format={{.}}", "abc", "0" * 12 + ";rm"]:
        with pytest.raises(DockerError):
            cli.inspect_raw([bad])
    assert cli.inspect_raw([]) == []


class _ReadOnlyDocker:
    """Records calls; any fault-capable method fails the test."""

    def __init__(self, infos):
        self.infos, self.calls = infos, []

    def project_container_ids(self, project):
        self.calls.append(("ps", project))
        return [f"{n:064x}" for n in range(len(self.infos))]

    def inspect_raw(self, ids):
        self.calls.append(("inspect", len(ids)))
        return self.infos

    def __getattr__(self, name):          # pause/unpause/stop/start/... must never be reached
        raise AssertionError(f"discovery called docker.{name}")


def test_run_discovery_is_read_only_and_ignores_other_projects():
    from resiliencepilot.discover import run_discovery
    stranger = _info("intruder", env=["X=http://databroker:1"])
    stranger["Config"]["Labels"]["com.docker.compose.project"] = "someone-else"
    docker = _ReadOnlyDocker(_stack() + [stranger])
    result = run_discovery(docker, PROJECT, _MAP, read_file=_FILES.get)
    assert [c[0] for c in docker.calls] == ["ps", "inspect"]
    assert "intruder" not in {n["name"] for n in result["nodes"]}
    assert len(result["nodes"]) == 6 and "diff_vs_dependency_map" in result


def test_render_text_lists_edges_and_diff():
    from resiliencepilot.discover import load_map, render_text, run_discovery
    from resiliencepilot.config import REPO_ROOT
    text = render_text(run_discovery(_ReadOnlyDocker(_stack()), PROJECT, load_map(REPO_ROOT), read_file=_FILES.get))
    assert "fms-forwarder    -> databroker       high" in text
    assert "discovered only    fms-forwarder -> csv-provider  (low confidence)" in text
    assert "not discoverable   resiliencepilot" in text
    assert "secret-influxdb-host-token" not in text


def test_cli_discover_json_and_out(tmp_path, monkeypatch, capsys):
    import json
    import resiliencepilot.__main__ as cli
    monkeypatch.setattr(cli, "DockerCLI", lambda: _ReadOnlyDocker(_stack()))
    monkeypatch.setattr(cli, "build", lambda *_: (_ for _ in ()).throw(AssertionError("safety objects built")))
    out = tmp_path / "sub" / "discovery.json"
    assert cli.main(["discover", "--json", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved == json.loads(printed[:printed.rindex("}") + 1])
    assert {e["from"] for e in saved["edges"]} >= {"csv-provider", "fms-forwarder", "fms-consumer"}
