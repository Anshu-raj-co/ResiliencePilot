"""Read-only dependency discovery: pure derivation from `docker inspect` records + diff against the curated map."""
import json

from resiliencepilot.config import REPO_ROOT
from resiliencepilot.discover import diff_against_map, discover, load_map, to_json


def c(name, service, env=(), args=(), mounts=(), depends="", aliases=(), net="proj_fms-backend"):
    """Minimal inspect record with the same shape Docker returns (fields we use only)."""
    return {
        "Name": f"/{name}", "Path": "/app/run", "Args": list(args),
        "Config": {"Image": f"img/{service}", "Env": list(env), "Healthcheck": None,
                   "Labels": {"com.docker.compose.project": "resiliencepilot-sdv", "com.docker.compose.service": service,
                              **({"com.docker.compose.depends_on": depends} if depends else {})}},
        "HostConfig": {"RestartPolicy": {"Name": "no"}},
        "NetworkSettings": {"Networks": {net: {"Aliases": [name, service, *aliases], "DNSNames": [name, service, "ab12cd34ef56"]}},
                            "Ports": {}},
        "Mounts": [{"Type": "bind", "Source": src, "Destination": dst} for src, dst in mounts],
    }


INFOS = [
    c("influxDB", "influxdb", env=["DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=super-secret-token-influxdb",
                                   "DOCKER_INFLUXDB_INIT_PASSWORD=pw-influxdb"]),
    c("fms-server", "fms-server", env=["INFLUXDB_URI=http://influxdb:8086"], depends="influxdb:service_healthy:false"),
    c("fms-consumer", "fms-consumer", env=["INFLUXDB_URI=http://influxdb:8086"], args=["zenoh", "-c", "/z.json5"],
      mounts=[("/run/desktop/mnt/host/c/x/config-client.json5", "/z.json5")], depends="influxdb:service_healthy:false"),
    c("fms-zenoh-router", "fms-zenoh-router"),
    c("databroker", "databroker"),
    c("fms-forwarder", "fms-forwarder", env=["KUKSA_DATABROKER_URI=http://databroker:55556"],
      mounts=[("/run/desktop/mnt/host/c/x/config-client.json5", "/z.json5")], depends="csv-provider:service_started:false"),
    c("csv-provider", "csv-provider", env=["KUKSA_DATA_BROKER_ADDR=databroker"]),
]
FILES = {"/run/desktop/mnt/host/c/x/config-client.json5": 'connect: { endpoints: [ "tcp/fms-zenoh-router:7447" ] }'}


def edges():
    return {(e.source, e.target): e for e in discover(INFOS, FILES.get)[1]}


def test_env_config_and_depends_on_evidence():
    e = edges()
    assert e[("fms-server", "influxDB")].kinds == ["env_reference", "depends_on"]
    assert e[("fms-forwarder", "databroker")].evidence[0]["detail"] == "KUKSA_DATABROKER_URI=http://databroker:55556"
    assert e[("fms-consumer", "fms-zenoh-router")].kinds[0] == "config_reference"
    assert e[("fms-forwarder", "csv-provider")].kinds == ["depends_on"]
    assert e[("fms-forwarder", "csv-provider")].confidence == "low"
    assert e[("csv-provider", "databroker")].confidence == "high"


def test_secrets_are_never_inspected_or_stored():
    nodes, found = discover(INFOS, FILES.get)
    blob = json.dumps(to_json(nodes, found, {}))
    assert "super-secret-token" not in blob and "pw-influxdb" not in blob


def test_no_self_edges_and_container_id_aliases_ignored():
    e = edges()
    assert all(s != t for s, t in e)
    nodes, _ = discover(INFOS, FILES.get)
    assert all("ab12cd34ef56" not in n.aliases for n in nodes)


def test_alias_matching_is_token_bounded():
    infos = [c("db", "db"), c("app", "app", env=["URL=http://dbx:1", "OTHER=mydb-backup", "OK=db.local"])]
    hits = [ev["detail"] for e in discover(infos, None)[1] for ev in e.evidence]
    assert hits == ["OK=db.local"], hits          # 'dbx' and 'mydb-backup' are other names, not 'db'


def test_missing_mount_files_are_skipped():
    nodes, found = discover(INFOS, lambda p: None)
    assert ("fms-consumer", "fms-zenoh-router") not in {(e.source, e.target) for e in found}


def test_diff_against_the_real_curated_map():
    _, found = discover(INFOS, FILES.get)
    d = diff_against_map(found, load_map(REPO_ROOT))
    assert {"from": "fms-server", "to": "influxDB", "evidence": ["env_reference", "depends_on"]} in d["matched"]
    assert any(x["map"] == "fms-zenoh-router -> fms-consumer" for x in d["direction_differs"])
    assert ("fms-forwarder", "csv-provider") in {(x["from"], x["to"]) for x in d["discovered_only"]}
    assert "resiliencepilot" in d["not_discoverable"]
    assert ("grafana", "influxDB") in {(x["from"], x["to"]) for x in d["map_only"]}   # grafana absent from this fixture


def test_file_paths_in_the_command_are_not_dependencies():
    infos = [c("app", "app"), c("worker", "worker", args=["--config", "/app/settings.json", "--peer", "app:9000"])]
    hits = [(e.source, e.target, ev["kind"], ev["detail"]) for e in discover(infos, None)[1] for ev in e.evidence]
    assert hits == [("worker", "app", "command_reference", "app:9000")]


def test_bare_words_in_mounted_files_are_not_host_references():
    files = {"/dash.json": '{"datasource": {"type": "influxdb"}}', "/ok.yaml": "url: http://influxdb:8086"}
    infos = [c("influxDB", "influxdb"),
             c("grafana", "grafana", mounts=[("/dash.json", "/etc/dash.json")]),
             c("grafana2", "grafana2", mounts=[("/ok.yaml", "/etc/ok.yaml")])]
    found = {(e.source, e.target) for e in discover(infos, files.get)[1]}
    assert ("grafana", "influxDB") not in found
    assert ("grafana2", "influxDB") in found
