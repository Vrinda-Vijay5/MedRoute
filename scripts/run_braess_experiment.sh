#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
source .venv/bin/activate
mkdir -p results

run_trial() {
  local label="$1"
  local link_state="$2"
  local mode="$3"
  local controller_log="$PROJECT_DIR/results/controller-braess-${label}-${mode}.log"
  export MEDROUTE_MODE="$mode"
  MEDROUTE_LINK_CAPACITY_BPS=100000000 \
    ryu-manager --observe-links medroute_controller.py \
    >"$controller_log" 2>&1 &
  local controller_pid=$!
  sleep 4
  if ! sudo -E "${VIRTUAL_ENV}/bin/python" braess_experiment.py \
    --label "$label" --candidate-link "$link_state" --routing-mode "$mode" \
    --controller-log "$controller_log" \
    --database "$PROJECT_DIR/results/medroute.db"; then
    kill "$controller_pid" 2>/dev/null || true
    sudo mn -c >/dev/null 2>&1 || true
    return 1
  fi
  kill "$controller_pid" 2>/dev/null || true
  wait "$controller_pid" 2>/dev/null || true
  sudo mn -c >/dev/null 2>&1 || true
}

run_trial before down qos
run_trial after_unprotected up qos
run_trial after_medroute up medroute

python - <<'PY'
import sqlite3

columns = (
    "scenario", "routing_mode", "latency_ms", "jitter_ms",
    "packet_loss_pct", "throughput_bps", "notes",
)
query = """
    SELECT scenario, routing_mode, latency_ms, jitter_ms,
           packet_loss_pct, throughput_bps, notes
    FROM experiments
    WHERE scenario LIKE 'braess_%'
    ORDER BY id DESC
    LIMIT 3
"""
with sqlite3.connect("results/medroute.db") as connection:
    rows = connection.execute(query).fetchall()
print("\t".join(columns))
for row in rows:
    print("\t".join("" if value is None else str(value) for value in row))
PY

echo "Compare the three actual rows and the matching controller logs."
echo "Call it a demonstrated Braess-type degradation only if after_unprotected is worse than before."
