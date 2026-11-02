#!/usr/bin/env bash
# ==============================================================================
# Quanifi — Sync Quantum Inspire Authentication to NiFi Docker Container
# ==============================================================================
# Usage:
#   ./tools/sync_qi_auth.sh [CONTAINER_NAME]
#
# Default container: quanifi-grover-nifi-1
# ==============================================================================

set -euo pipefail

CONTAINER="${1:-quanifi-grover-nifi-1}"
HOST_CONFIG_DIR="$HOME/.quantuminspire"
HOST_CONFIG="$HOST_CONFIG_DIR/config.json"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Color helpers
C_RESET="\033[0m"
C_GREEN="\033[1;32m"
C_BLUE="\033[1;34m"
C_YELLOW="\033[1;33m"
C_RED="\033[1;31m"
C_CYAN="\033[1;36m"

echo -e "${C_CYAN}================================================================${C_RESET}"
echo -e "${C_CYAN}   Quanifi — Quantum Inspire Authentication & Docker Sync       ${C_RESET}"
echo -e "${C_CYAN}================================================================${C_RESET}"

# 1. Check if Docker container is running
echo -e "\n${C_BLUE}[1/4] Checking Docker container '${CONTAINER}'...${C_RESET}"
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo -e "${C_RED}Error: Container '${CONTAINER}' is not running.${C_RESET}"
  echo -e "Start it first or specify container name: $0 <container-name>"
  exit 1
fi
echo -e "${C_GREEN}✓ Container '${CONTAINER}' is running.${C_RESET}"

# 2. Check if host has ~/.quantuminspire/config.json
echo -e "\n${C_BLUE}[2/4] Checking host credentials at ${HOST_CONFIG}...${C_RESET}"
if [ ! -f "$HOST_CONFIG" ]; then
  echo -e "${C_YELLOW}No credentials file found. Initiating Quantum Inspire login...${C_RESET}"
  echo -e "Follow the browser prompt to log in with your Quantum Inspire account:"
  
  if command -v qi &>/dev/null; then
    qi login
  elif [ -x "$REPO_ROOT/.venv/bin/qi" ]; then
    "$REPO_ROOT/.venv/bin/qi" login
  else
    echo -e "${C_RED}Error: 'qi' CLI tool not found. Install with: pip install quantuminspire${C_RESET}"
    exit 1
  fi
fi

if [ ! -f "$HOST_CONFIG" ]; then
  echo -e "${C_RED}Error: Login failed or ${HOST_CONFIG} was not created.${C_RESET}"
  exit 1
fi
echo -e "${C_GREEN}✓ Found credentials file on host.${C_RESET}"

# 3. Copy credentials into the Docker container
echo -e "\n${C_BLUE}[3/4] Copying credentials into '${CONTAINER}'...${C_RESET}"
# The file goes to the nifi user's home and to the same path as on the host.
docker exec -u root "$CONTAINER" mkdir -p /home/nifi/.quantuminspire "$HOST_CONFIG_DIR"
docker cp "$HOST_CONFIG" "${CONTAINER}:/home/nifi/.quantuminspire/config.json"
docker cp "$HOST_CONFIG" "${CONTAINER}:$HOST_CONFIG_DIR/config.json"
docker exec -u root "$CONTAINER" chown -R nifi:nifi /home/nifi/.quantuminspire "$HOST_CONFIG_DIR"
docker exec -u root "$CONTAINER" chmod 600 /home/nifi/.quantuminspire/config.json "$HOST_CONFIG_DIR/config.json"
echo -e "${C_GREEN}✓ Credentials successfully copied & permissions secured.${C_RESET}"

# 4. Verify connection inside the container
echo -e "\n${C_BLUE}[4/4] Verifying connection to Quantum Inspire from Docker container...${C_RESET}"
TEST_CMD='
import sys
from qiskit_quantuminspire.qi_provider import QIProvider
try:
    provider = QIProvider()
    backends = provider.backends()
    print(f"CONNECTED: Found {len(backends)} available backends:")
    for b in backends:
        print(f"  * {b.name} ({b.num_qubits} qubits)")
except Exception as e:
    print(f"FAILED: {e}", file=sys.stderr)
    sys.exit(1)
'

VENV_PYTHON="/opt/nifi/nifi-current/work/python/extensions/QuantumInspireBatchSubmitter/0.1.0/bin/python3"

if docker exec "$CONTAINER" "$VENV_PYTHON" -c "$TEST_CMD"; then
  echo -e "\n${C_GREEN}================================================================${C_RESET}"
  echo -e "${C_GREEN}✓ Quantum Inspire is 100% READY in NiFi!                        ${C_RESET}"
  echo -e "${C_GREEN}  You can now run Tuna-17 or QX emulator in your Grover flow.  ${C_RESET}"
  echo -e "${C_GREEN}================================================================${C_RESET}"
else
  echo -e "\n${C_RED}Warning: Could not connect from container. Check token validity.${C_RESET}"
  exit 1
fi
