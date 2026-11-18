#!/usr/bin/env bash
# End-to-end demo: build labelled source material, insert freeze clips with
# each audio strategy and several durations, then verify every output.
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

mkdir -p work out

python3 freeze_edit/make_source.py work/source.mp4 --duration 10 --fps 30

# Same freeze length, all three audio strategies.
for mode in mute extend continue; do
  python3 freeze_edit/freeze_insert.py work/source.mp4 \
    "out/freeze_${mode}_2s.mp4" --at 4.0 --duration 2.0 --audio "$mode" >/dev/null
done

# Different freeze lengths prove downstream clips shift by exactly the delta.
python3 freeze_edit/freeze_insert.py work/source.mp4 \
  out/freeze_mute_1p5s.mp4 --at 4.0 --duration 1.5 --audio mute >/dev/null
python3 freeze_edit/freeze_insert.py work/source.mp4 \
  out/freeze_mute_3p5s.mp4 --at 4.0 --duration 3.5 --audio mute >/dev/null

# Non-integer freeze point exercises frame-boundary snapping.
python3 freeze_edit/freeze_insert.py work/source.mp4 \
  out/freeze_extend_2p7s_at6p3.mp4 --at 6.3 --duration 2.7 --audio extend >/dev/null

status=0
for spec in out/*.mp4.json; do
  video="${spec%.json}"
  echo "===== $(basename "$video") ====="
  python3 freeze_edit/verify_freeze.py "$video" work/source.mp4 --spec "$spec" \
    || status=1
  echo
done

# Export the monotonically increasing PTS list for the headline output.
ffprobe -v error -select_streams v:0 -show_entries frame=pts_time \
  -of csv=p=0 out/freeze_mute_2s.mp4 | sed 's/,$//' \
  > out/freeze_mute_2s.timestamps.txt

exit "$status"
