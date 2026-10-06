#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
source .venv/bin/activate
mkdir -p results

for mode in baseline qos medroute; do
  for scenario in normal congestion loss delay jitter; do
    export MEDROUTE_MODE="$mode"
    export MEDROUTE_DB="results/medroute.db"
    ryu-manager --observe-links medroute_controller.py \
      >"results/controller-${mode}-${scenario}.log" 2>&1 &
    controller_pid=$!
    sleep 4
    if ! sudo -E "${VIRTUAL_ENV}/bin/python" experiment_runner.py \
      --scenario "$scenario" --routing-mode "$mode" --traffic-type ECG --duration 10; then
      kill "$controller_pid" 2>/dev/null || true
      sudo mn -c >/dev/null 2>&1 || true
      exit 1
    fi
    kill "$controller_pid" 2>/dev/null || true
    wait "$controller_pid" 2>/dev/null || true
    sudo mn -c >/dev/null 2>&1 || true
  done
done

python analysis/generate_plots.py
echo "Actual measurements: results/medroute.db, results/raw, and results/plots"
