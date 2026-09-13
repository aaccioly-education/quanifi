#!/usr/bin/env bash
set -euo pipefail

NIFI_HOME="~/projects/nifi/nifi-2.11.0"
BASE_URL="https://127.0.0.1:8446/nifi-api"
QUANIFI_DIR="~/projects/quanifi"

mine_jvm() {
    for pid in $({ pgrep -f "org.apache.nifi" || true; }); do
        if ps -p "$pid" -o command= 2>/dev/null | grep -q -- "$NIFI_HOME"; then
            echo "$pid"
        fi
    done
}

stop() {
    echo "Stopping NiFi 2.11.0 scoped to $NIFI_HOME..."
    pids="$(mine_jvm)"
    if [ -z "$pids" ]; then
        echo "2.11.0 is not running"
    else
        for pid in $pids; do kill -TERM "$pid" 2>/dev/null || true; done
        for _ in $(seq 1 20); do
            [ -z "$(mine_jvm)" ] && break
            sleep 2
        done
        if [ -n "$(mine_jvm)" ]; then
            echo "Graceful stop timed out; force killing..."
            for pid in $(mine_jvm); do kill -9 "$pid" 2>/dev/null || true; done
            sleep 2
        fi
    fi
    pattern="$NIFI_HOME/./python/framework/Controller.py"
    n=$({ pgrep -f "$pattern" || true; } | wc -l | tr -d ' ')
    if [ "${n:-0}" != "0" ]; then
        pkill -f "$pattern" || true
        sleep 1
        echo "Reaped $n orphaned Python controller process(es) for 2.11.0"
    fi
    echo "NiFi 2.11.0 stopped."
}

start() {
    export NIFI_USER="admin"
    export NIFI_PASSWORD="quanifiadmin123"
    export PATH="$QUANIFI_DIR/.venv/bin:$HOME/.local/bin:$PATH"
    export UV_PYTHON="$QUANIFI_DIR/.venv/bin/python3"
    export UV_CONSTRAINT="$QUANIFI_DIR/nifi_extensions_matrix/venv-constraints.txt"

    cd "$NIFI_HOME"
    log="$NIFI_HOME/logs/nifi-app.log"
    if [ -f "$log" ]; then
        read -r start_inode start_offset < <(stat -f '%i %z' "$log")
    else
        start_inode=0
        start_offset=0
    fi

    echo "Launching NiFi 2.11.0..."
    nohup ./bin/nifi.sh start >/dev/null 2>&1 &

    echo "Waiting for NiFi 2.11.0 to become ready..."
    cd "$QUANIFI_DIR"
    .venv/bin/python tools/nifi_ready.py \
        --log "$NIFI_HOME/logs/nifi-app.log" \
        --flow "$NIFI_HOME/conf/flow.json.gz" \
        --start-inode "$start_inode" \
        --start-offset "$start_offset" \
        --base-url "$BASE_URL" --stall-seconds 240 --quiet-seconds 30
}

case "${1:-status}" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        stop
        start
        ;;
    status)
        pids="$(mine_jvm)"
        if [ -n "$pids" ]; then
            echo "NiFi 2.11.0 is running with PID(s): $pids"
        else
            echo "NiFi 2.11.0 is not running"
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
