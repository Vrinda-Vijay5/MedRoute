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
mkdir -p results/raw

REPETITIONS="${BRAESS_REPETITIONS:-3}"
DURATION="${DURATION:-10}"
RANDOM_SEED="${RANDOM_SEED:-308}"
RUN_ID="${RUN_ID:-braess-$(date -u +%Y%m%dT%H%M%SZ)}"
DATABASE="${MEDROUTE_DB:-$PROJECT_DIR/results/medroute.db}"
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

run_trial() {
  local trial_index="$1"
  local repetition="$2"
  local label="$3"
  local link_state="$4"
  local mode="$5"
  local controller_log="$PROJECT_DIR/results/controller-${RUN_ID}-t${trial_index}-${label}-${mode}.log"
  local capacity_bps
  capacity_bps="$("${VIRTUAL_ENV}/bin/python" -c \
    'from braess_config import INTERSWITCH_CAPACITY_BPS; print(INTERSWITCH_CAPACITY_BPS)')"
  cleanup_trial
  export MEDROUTE_MODE="$mode"
  export MEDROUTE_DB="$DATABASE"
  export MEDROUTE_LINK_CAPACITY_BPS="$capacity_bps"
  ryu-manager --observe-links medroute_controller.py >"$controller_log" 2>&1 &
  controller_pid=$!
  sleep 2
  sudo -E "${VIRTUAL_ENV}/bin/python" braess_experiment.py \
    --label "$label" --candidate-link "$link_state" --routing-mode "$mode" \
    --duration "$DURATION" --controller-log "$controller_log" \
    --database "$DATABASE" --run-id "$RUN_ID" --trial-index "$trial_index"
  cleanup_trial
}

mapfile -t trials < <(python - "$REPETITIONS" "$RANDOM_SEED" <<'PY'
import random
import sys

items = []
for repetition in range(1, int(sys.argv[1]) + 1):
    items.extend([
        (repetition, "before", "down", "qos"),
        (repetition, "after_unprotected", "up", "qos"),
        (repetition, "after_medroute", "up", "medroute"),
    ])
random.Random(int(sys.argv[2])).shuffle(items)
for item in items:
    print("\t".join(map(str, item)))
PY
)

printf 'run_id\ttrial\trepetition\tcondition\tlink_state\tmode\n' \
  >"results/${RUN_ID}-trial-order.tsv"
trial_index=0
for trial in "${trials[@]}"; do
  IFS=$'\t' read -r repetition label link_state mode <<<"$trial"
  trial_index=$((trial_index + 1))
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$RUN_ID" "$trial_index" "$repetition" "$label" "$link_state" "$mode" \
    >>"results/${RUN_ID}-trial-order.tsv"
  run_trial "$trial_index" "$repetition" "$label" "$link_state" "$mode"
done

python - "$DATABASE" "$RUN_ID" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    rows = connection.execute(
        """SELECT scenario, routing_mode, latency_ms, jitter_ms,
                  packet_loss_pct, throughput_bps, selected_path_json, raw_path
           FROM experiments WHERE run_id=? ORDER BY id""", (sys.argv[2],)
    ).fetchall()
print("scenario\tmode\tlatency_ms\tjitter_ms\tloss_pct\tthroughput_bps\tpath\traw")
for row in rows:
    print("\t".join("" if value is None else str(value) for value in row))
PY

echo "New dedicated route-safety evidence run: $RUN_ID"
echo "Call it a demonstrated Braess-type degradation only if repeated after_unprotected outcomes are worse than matched before outcomes."
