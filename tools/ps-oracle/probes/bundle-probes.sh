#!/bin/sh
# Build the Photoshop probe bundle: generate probes (photocraft-cli), dry-run ps_runner.jsx on the
# mock, and pack ps-probe/ into a tarball for the Mac.
# Usage: ./bundle-probes.sh [out.tar.gz=/workspace/ps-probe-bundle.tar.gz]
set -eu
here=$(cd "$(dirname "$0")" && pwd)
oracle=$(dirname "$here")
out=${1:-/workspace/ps-probe-bundle.tar.gz}
cli=${PHOTOCRAFT_CLI:-/workspace/photocraft/target/agent-assess/debug/photocraft-cli}
py=${PYTHON:-/tmp/venv/bin/python}
stage=$here/stage
rm -rf "$stage"; mkdir -p "$stage"
"$py" "$here/gen_probes.py" --cli "$cli" --stage "$stage"
cp "$oracle/ps_runner.jsx" "$oracle/run-photoshop.sh" "$here/README-mac.md" "$stage/"
chmod +x "$stage/run-photoshop.sh"
# mock dry run (ES3 parse + every case through the runner, Track J descriptors type-checked)
info=$(mktemp -d); mock=$(mktemp -d)
for f in "$stage"/psds/*.psd; do "$cli" info --compact "$f" > "$info/$(basename "$f" .psd).json"; done
node "$oracle/test/check_jsx.js" "$stage/ps_runner.jsx" "$stage" "$info" "$mock" | grep -v '^WARN' | tail -3
rm -rf "$info" "$mock"
tmp=$(mktemp -d); cp -r "$stage" "$tmp/ps-probe"
(cd "$tmp" && COPYFILE_DISABLE=1 tar -czf "$out" ps-probe)
rm -rf "$tmp"
echo "bundle: $out ($(du -h "$out" | cut -f1))"
