#!/usr/bin/env bash
# Render-speed bench for the paper-cut style (HyperFrames, 1080x1920, 30 fps).
#   bench.sh <shadow:clones|filter> <grain:raster|turbulence> <seconds> [out_dir]
# Copies the example to a temp dir, patches the variant and duration, renders,
# prints wall time and the MP4 size, then deletes the render (disk hygiene).
# The scene's motion is keyed to 20 s, so any duration up to 20 s is a moving scene.
set -euo pipefail
shadow=${1:?shadow}; grain=${2:?grain}; secs=${3:?seconds}
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/paper-cut-bench.XXXXXX")
trap 'rm -rf "$work"' EXIT
cp "$here"/index.html "$here"/ink-theater.js "$here"/paper-cut.js "$work"/
sed -i.bak \
  -e "s/var VARIANT = { shadow: \"clones\", grain: \"raster\" };/var VARIANT = { shadow: \"$shadow\", grain: \"$grain\" };/" \
  -e "s/var DURATION = 5;/var DURATION = $secs;/" \
  -e "s/data-duration=\"5\"/data-duration=\"$secs\"/g" "$work/index.html"
grep -q "shadow: \"$shadow\", grain: \"$grain\"" "$work/index.html" || { echo "variant patch failed" >&2; exit 2; }
grep -q "var DURATION = $secs;" "$work/index.html" || { echo "duration patch failed" >&2; exit 2; }
export HYPERFRAMES_NO_TELEMETRY=1
t0=$(date +%s)
npx --yes "${CONTRECHAMP_HYPERFRAMES_SPEC:-hyperframes}" render "$work" --output "$work/out.mp4" --fps 30 --quiet >"$work/log.txt" 2>&1 || { tail -20 "$work/log.txt"; exit 1; }
t1=$(date +%s)
frames=$(ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of csv=p=0 "$work/out.mp4")
echo "variant=$shadow+$grain seconds=$secs frames=$frames wall_s=$((t1 - t0)) bytes=$(stat -f%z "$work/out.mp4" 2>/dev/null || stat -c%s "$work/out.mp4")"
if [ -n "${4:-}" ]; then mkdir -p "$4"; cp "$work/out.mp4" "$4/${shadow}_${grain}_${secs}s.mp4"; fi
