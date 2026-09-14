#!/usr/bin/env bash
# Foreground NiFi bootstrap owned by the scoped launchd job from `just nifi-start`.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
nifi_home="${NIFI_HOME:?Set NIFI_HOME to the NiFi installation directory}"
env_file="${1:-.env}"

if [ -f "$repo_root/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$repo_root/.env"
    set +a
fi
if [ "$env_file" != .env ]; then
    if [[ "$env_file" != /* ]]; then
        env_file="$repo_root/$env_file"
    fi
    if [ -f "$env_file" ]; then
        set -a
        # shellcheck disable=SC1090
        . "$env_file"
        set +a
    fi
fi

export PATH="$repo_root/.venv/bin:$HOME/.local/bin:$PATH"
cd "$nifi_home"
exec ./bin/nifi.sh run
