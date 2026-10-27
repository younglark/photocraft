#!/bin/sh
# Run the ps-oracle reference cases in the installed Adobe Photoshop (macOS).
# Usage: ./run-photoshop.sh [bundle_dir=.] [out_dir=<bundle>/ps-out] [case-id-regex]
# Photoshop is addressed by bundle id (com.adobe.Photoshop), so any installed version is used.
# The script SOURCE is passed to `do javascript` (Photoshop 27.x fails `do javascript file`
# with error 8800). osascript blocks until the script returns; we then check the DONE marker.
# First run: macOS asks to let your terminal control Adobe Photoshop (Automation) - allow it.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
bundle=$(cd "${1:-$here}" && pwd)
out=${2:-$bundle/ps-out}
filter=${3:-}
mkdir -p "$out"
out=$(cd "$out" && pwd)
[ -f "$bundle/cases.json" ] || { echo "no cases.json in $bundle" >&2; exit 2; }
jsx="$bundle/ps_runner.jsx"
[ -f "$jsx" ] || jsx="$here/ps_runner.jsx"
rm -f "$out/DONE"
echo "ps-oracle: bundle=$bundle out=$out filter=${filter:-<all>}"
start=$(date +%s)
set +e
osascript - "$jsx" "$bundle" "$out" "$filter" <<'APPLESCRIPT'
on run argv
	set src to read (POSIX file (item 1 of argv)) as «class utf8»
	set jsArgs to items 2 thru -1 of argv
	with timeout of 36000 seconds
		tell application id "com.adobe.Photoshop"
			return do javascript src with arguments jsArgs
		end tell
	end timeout
end run
APPLESCRIPT
rc=$?
set -e
echo "elapsed: $(( $(date +%s) - start ))s (osascript exit $rc)"
if [ -f "$out/DONE" ]; then
  echo "DONE: $(cat "$out/DONE")"
  echo "results: $out/results.json"
  exit 0
fi
echo "Photoshop run did not finish (no $out/DONE). Partial results, if any: $out/results.json" >&2
exit 1
