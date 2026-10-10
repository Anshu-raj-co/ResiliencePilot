#!/usr/bin/env bash
# Pull prebuilt images and start the SDV sandbox (no local builds).
source "$(dirname "$0")/_compose.sh"

if [ ! -f "$BLUEPRINT_DIR/fms-blueprint-compose.yaml" ]; then
  echo "Blueprint missing. Run: git submodule update --init" >&2
  exit 1
fi

# Files bind-mounted into Linux containers must have LF endings. On Windows with core.autocrlf=true
# they are checked out as CRLF and influxdb's init script then fails with "No such file or directory".
# Stripping CR is content-preserving for git (see docs/DECISIONS.md D-005).
for f in influxdb/init-scripts/create-fms-token.sh config/zenoh/config-client.json5 \
         config/zenoh/config-router.json5 csv-provider/signalsFmsRecording.csv spec/overlay/vss.json \
         grafana/provisioning/dashboards/dashboards_from_filesystem.yaml grafana/dashboards/FMS-Fleet.json; do
  if grep -q $'\r' "$BLUEPRINT_DIR/$f"; then
    sed -i 's/\r$//' "$BLUEPRINT_DIR/$f"
    echo "normalized line endings: $f"
  fi
done

rp_compose pull "${RP_SERVICES[@]}"
rp_compose up --detach --no-build "${RP_SERVICES[@]}"
rp_compose ps
