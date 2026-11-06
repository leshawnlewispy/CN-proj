#!/usr/bin/env bash
# Runs every scenario in both modes, then the analysis.
#   sudo -E ./experiments/run_all.sh            (kernel OVS, e.g. the Mininet VM)
#   sudo -E ./experiments/run_all.sh --user-dp  (userspace OVS, e.g. containers)
# Env: DURATION (default 30 s per run), RYU (path to ryu-manager if not on PATH)
set -e
cd "$(dirname "$0")/.."
D=${DURATION:-30}
run() { python3 experiments/run.py --duration "$D" "$@"; mn -c >/dev/null 2>&1 || true; }

for s in E1 E2 E3 E4 E5; do
  for m in static adaptive; do run --scenario "$s" --mode "$m" "$@"; done
done
for t in 1 2 4 8; do
  for m in static adaptive; do run --scenario E6 --mode "$m" --threads "$t" "$@"; done
done
python3 analysis/analyze.py
