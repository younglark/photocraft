#!/bin/sh
# Pack the Photoshop-side bundle: PSDs + cases.json + ps_runner.jsx + run-photoshop.sh + README.
# Usage: tools/ps-oracle/bundle.sh [corpus_dir=/workspace/photocraft/corpus] [out=/workspace/ps-oracle-bundle.tar.gz]
# Stages into <out without .tar.gz>/ps-oracle/ (also used by the box runner as --bundle).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
corpus=${1:-/workspace/photocraft/corpus}
out=${2:-/workspace/ps-oracle-bundle.tar.gz}
stage=${out%.tar.gz}
rm -rf "$stage"
mkdir -p "$stage/ps-oracle/psds"
python3 - "$here/cases.json" "$corpus" "$stage/ps-oracle/psds" <<'PY'
import json, os, shutil, sys
m = json.load(open(sys.argv[1]))
for f in m["files"]:
    rel = f["psd"][len("psds/"):]
    dst = os.path.join(sys.argv[3], rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(os.path.join(sys.argv[2], rel), dst)
print(len(m["files"]), "PSDs,", len(m["cases"]), "cases")
PY
cp "$here/cases.json" "$here/ps_runner.jsx" "$here/run-photoshop.sh" "$here/README.md" "$stage/ps-oracle/"
chmod +x "$stage/ps-oracle/run-photoshop.sh"
(cd "$stage" && COPYFILE_DISABLE=1 tar -czf "$out" ps-oracle)
echo "bundle: $out ($(du -h "$out" | cut -f1)); staged at $stage/ps-oracle"
