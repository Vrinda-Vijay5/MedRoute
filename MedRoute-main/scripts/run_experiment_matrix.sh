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
mkdir -p results/raw results/plots

REPETITIONS="${REPETITIONS:-3}"
DURATION="${DURATION:-10}"
TRAFFIC_TYPE="${TRAFFIC_TYPE:-ECG}"
RANDOM_SEED="${RANDOM_SEED:-308}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
DATABASE="${MEDROUTE_DB:-$PROJECT_DIR/results/medroute.db}"

if ! [[ "$REPETITIONS" =~ ^[1-9][0-9]*$ ]]; then
  echo "REPETITIONS must be a positive integer" >&2
  exit 2
fi

controller_pid=""
cleanup_trial() {
  if [[ -n "$controller_pid" ]]; then
    kill "$controller_pid" 2>/dev/null || true
    wait "$controller_pid" 2>/dev/null || true
    controller_pid=""
  fi
  sudo mn -c >/dev/null 2>&1 || true
}
trap cleanup_trial EXIT

mapfile -t trials < <(python - "$REPETITIONS" "$RANDOM_SEED" <<'PY'
import random
import sys

repetitions = int(sys.argv[1])
seed = int(sys.argv[2])
trials = [
    (mode, scenario, repetition)
    for repetition in range(1, repetitions + 1)
    for mode in ("baseline", "qos", "medroute")
    for scenario in ("normal", "congestion", "loss", "delay", "jitter")
]
random.Random(seed).shuffle(trials)
for mode, scenario, repetition in trials:
    print("{}\t{}\t{}".format(mode, scenario, repetition))
PY
)

printf 'run_id\ttrial\trepetition\tmode\tscenario\n' >"results/${RUN_ID}-trial-order.tsv"
trial_index=0
for trial in "${trials[@]}"; do
  IFS=$'\t' read -r mode scenario repetition <<<"$trial"
  trial_index=$((trial_index + 1))
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$RUN_ID" "$trial_index" "$repetition" "$mode" "$scenario" \
    >>"results/${RUN_ID}-trial-order.tsv"
  cleanup_trial
  export MEDROUTE_MODE="$mode"
  export MEDROUTE_DB="$DATABASE"
  export MEDROUTE_LINK_CAPACITY_BPS=100000000
  controller_log="results/controller-${RUN_ID}-t${trial_index}-${mode}-${scenario}.log"
  ryu-manager --observe-links medroute_controller.py >"$controller_log" 2>&1 &
  controller_pid=$!
  sleep 2
  sudo -E "${VIRTUAL_ENV}/bin/python" experiment_runner.py \
    --scenario "$scenario" --routing-mode "$mode" \
    --traffic-type "$TRAFFIC_TYPE" --duration "$DURATION" \
    --database "$DATABASE" --run-id "$RUN_ID" --trial-index "$trial_index"
  cleanup_trial
done

python analysis/generate_plots.py --database "$DATABASE" --run-id "$RUN_ID"
echo "New evidence run: $RUN_ID"
echo "Trial order: results/${RUN_ID}-trial-order.tsv"
echo "Raw evidence: results/raw; database: $DATABASE; plots/tables: results/plots"
