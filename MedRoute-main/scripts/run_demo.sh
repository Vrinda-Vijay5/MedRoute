#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
VENV_DIR="${MEDROUTE_VENV:-$PROJECT_DIR/.venv}"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Linux virtualenv not found at $VENV_DIR; set MEDROUTE_VENV to the existing WSL venv" >&2
  exit 2
fi
source "$VENV_DIR/bin/activate"
mkdir -p results

CONTROLLER_PID=""
DASHBOARD_PID=""
GATEWAY_PID=""
cleanup() {
  for pid in "$CONTROLLER_PID" "$DASHBOARD_PID" "$GATEWAY_PID"; do
    if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; fi
  done
  sudo mn -c >/dev/null 2>&1 || true
}
trap cleanup EXIT

export MEDROUTE_MODE="${MEDROUTE_MODE:-medroute}"
export MEDROUTE_DB="${MEDROUTE_DB:-$PROJECT_DIR/results/medroute.db}"
export MEDROUTE_LINK_CAPACITY_BPS="${MEDROUTE_LINK_CAPACITY_BPS:-100000000}"

python dashboard_server.py --database "$MEDROUTE_DB" \
  >results/dashboard.log 2>&1 &
DASHBOARD_PID=$!

# This remains visibly TEST mode until a real device/app/API adapter is agreed
# and verified. It does not generate readings by itself.
python patient_gateway.py --database "$MEDROUTE_DB" --mode test \
  --adapter-name test-webhook >results/patient-gateway.log 2>&1 &
GATEWAY_PID=$!

ryu-manager --observe-links medroute_controller.py \
  >results/controller-demo.log 2>&1 &
CONTROLLER_PID=$!

sleep 3
echo "Dashboard: http://127.0.0.1:8080"
echo "Patient gateway is TEST mode at http://127.0.0.1:8091"
echo "No patient reading is generated automatically."
echo "Controller mode: $MEDROUTE_MODE; evidence: $MEDROUTE_DB"
sudo -E "${VIRTUAL_ENV}/bin/python" hospital_topology.py --scenario "${1:-normal}"
