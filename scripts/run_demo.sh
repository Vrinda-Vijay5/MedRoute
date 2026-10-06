#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
source .venv/bin/activate
mkdir -p results

CONTROLLER_PID=""
cleanup() {
  if [[ -n "$CONTROLLER_PID" ]]; then kill "$CONTROLLER_PID" 2>/dev/null || true; fi
  sudo mn -c >/dev/null 2>&1 || true
}
trap cleanup EXIT

export MEDROUTE_MODE="${MEDROUTE_MODE:-medroute}"
ryu-manager --observe-links medroute_controller.py 2>&1 | tee results/controller-demo.log &
CONTROLLER_PID=$!
sleep 4
sudo -E "${VIRTUAL_ENV}/bin/python" hospital_topology.py --scenario "${1:-normal}"

