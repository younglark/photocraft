#!/bin/sh
# Box side: (re)run photocraft-cli on every case, then diff against Photoshop's outputs.
# Usage: tools/ps-oracle/compare.sh <ps_output_dir> [report_dir=/workspace/pso-report]
# Env: CLI (default <repo>/target/release/photocraft-cli), BUNDLE (default /workspace/ps-oracle-bundle/ps-oracle),
#      BOX_OUT (default /workspace/pso-box), FILTER (case-id regex), NO_RUN=1 to reuse BOX_OUT.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
[ $# -ge 1 ] || { echo "usage: $0 <ps_output_dir> [report_dir]" >&2; exit 2; }
ps=$(cd "$1" && pwd)
report=${2:-/workspace/pso-report}
cli=${CLI:-$repo/target/release/photocraft-cli}
bundle=${BUNDLE:-/workspace/ps-oracle-bundle/ps-oracle}
box=${BOX_OUT:-/workspace/pso-box}
[ -f "$bundle/cases.json" ] || "$here/bundle.sh"
if [ "${NO_RUN:-}" != 1 ]; then
  rm -rf "$box"
  python3 "$here/run_photocraft.py" --cli "$cli" --bundle "$bundle" --out "$box" ${FILTER:+--filter "$FILTER"}
fi
rm -rf "$report"
python3 "$here/compare.py" --manifest "$bundle/cases.json" --ps "$ps" --box "$box" --report "$report"
