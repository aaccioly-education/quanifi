#!/bin/sh -e
# Quanifi wrapper around the official apache/nifi start script. On every start:
#  1. apply Quanifi's nifi.properties settings (conf/ is a volume, so settings
#     baked into the image would never reach an existing one);
#  2. seed the quickstart canvas into an empty conf/ volume;
#  3. start the log-only readiness monitor (the HEALTHCHECK reads its status file;
#     nothing polls the NiFi API during startup, which can wedge NiFi 2.9);
#  4. exec the official start script.
scripts_dir=/opt/nifi/scripts
. "${scripts_dir}/common.sh"

prop_set() {
    if grep -q "^$1=" "${nifi_props_file}"; then
        prop_replace "$1" "$2"
    else
        printf '%s=%s\n' "$1" "$2" >> "${nifi_props_file}"
    fi
}

prop_set nifi.python.extensions.source.directory.quanifi /opt/quanifi/nifi_extensions
prop_set nifi.flowcontroller.autoResumeState "${QUANIFI_AUTO_RESUME:-false}"
if [ -n "${QUANIFI_PYTHON_MAX_PROCESSES:-}" ]; then
    prop_set nifi.python.max.processes "${QUANIFI_PYTHON_MAX_PROCESSES}"
fi
if [ -n "${QUANIFI_PYTHON_MAX_PROCESSES_PER_TYPE:-}" ]; then
    prop_set nifi.python.max.processes.per.extension.type "${QUANIFI_PYTHON_MAX_PROCESSES_PER_TYPE}"
fi

flow="${NIFI_HOME}/conf/flow.json.gz"
if [ ! -f "${flow}" ] && [ -f /opt/quanifi/flow/flow.json.gz ]; then
    cp /opt/quanifi/flow/flow.json.gz "${flow}"
    echo "[quanifi] seeded the quickstart canvas into conf/flow.json.gz"
fi

reports="${NIFI_HOME}/reports"
if [ ! -w "${reports}" ]; then
    echo "[quanifi] WARNING: ${reports} is not writable by uid $(id -u); reports will fail." \
         "On Linux: mkdir -p reports && sudo chown $(id -u):$(id -g) reports"
fi

log="${NIFI_HOME}/logs/nifi-app.log"
start_inode=0
start_offset=0
if [ -f "${log}" ]; then
    start_inode=$(stat -c %i "${log}")
    start_offset=$(stat -c %s "${log}")
fi
mkdir -p /tmp/quanifi
python3 /opt/quanifi/bin/quanifi_monitor.py reset --status /tmp/quanifi/status.json
python3 /opt/quanifi/bin/quanifi_monitor.py watch \
    --log "${log}" --flow "${flow}" --props "${nifi_props_file}" \
    --manifest /opt/quanifi/manifest.json --status /tmp/quanifi/status.json \
    --recoveries /tmp/quanifi/recoveries \
    --start-inode "${start_inode}" --start-offset "${start_offset}" &

exec "${scripts_dir}/start.sh" "$@"
