#!/bin/sh
# Box side, after the Mac run:  ./run-probes-compare.sh <probe-out dir or .tar.gz> [report dir] [filter]
# Uses the photocraft-cli build from PHOTOCRAFT_CLI (default: the agent-assess debug build).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
stage=${STAGE:-$here/stage}
cli=${PHOTOCRAFT_CLI:-/workspace/photocraft/target/agent-assess/debug/photocraft-cli}
py=${PYTHON:-/tmp/venv/bin/python}
src=$1; report=${2:-/workspace/probe-report}; filter=${3:-}
if [ -f "$src" ]; then d=$(mktemp -d); tar -xzf "$src" -C "$d"; src=$(dirname "$(find "$d" -name results.json | head -1)"); fi
"$py" "$here/probe_compare.py" --stage "$stage" --ps "$src" --cli "$cli" --report "$report" ${filter:+--filter "$filter"}
