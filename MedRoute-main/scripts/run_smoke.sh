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

RUN_ID="${RUN_ID:-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
DATABASE="${MEDROUTE_DB:-$PROJECT_DIR/results/medroute.db}"
controller_pid=""
cleanup() {
  if [[ -n "$controller_pid" ]]; then
    kill "$controller_pid" 2>/dev/null || true
    wait "$controller_pid" 2>/dev/null || true
  fi
  sudo mn -c >/dev/null 2>&1 || true
}
trap cleanup EXIT

sudo mn -c >/dev/null 2>&1 || true
export MEDROUTE_MODE=medroute
export MEDROUTE_DB="$DATABASE"
export MEDROUTE_LINK_CAPACITY_BPS=100000000
ryu-manager --observe-links medroute_controller.py \
  >"results/controller-${RUN_ID}.log" 2>&1 &
controller_pid=$!
sleep 2
sudo -E "${VIRTUAL_ENV}/bin/python" experiment_runner.py \
  --scenario normal --routing-mode medroute --traffic-type ECG --duration 5 \
  --database "$DATABASE" --run-id "$RUN_ID" --trial-index 1
echo "Smoke evidence run: $RUN_ID"
