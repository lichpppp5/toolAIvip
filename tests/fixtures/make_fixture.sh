#!/usr/bin/env bash
# tests/fixtures/make_fixture.sh
#
# Generate the synthetic 5-second test fixture for LiveGuard M1 tests.
# Requires: FFmpeg 6.1+ with libx264 and aac encoder support.
#
# Usage:
#   bash tests/fixtures/make_fixture.sh
#
# Output:
#   tests/fixtures/sample_5s.mp4

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT="${SCRIPT_DIR}/sample_5s.mp4"

if ! command -v ffmpeg &>/dev/null; then
  echo "ERROR: ffmpeg not found on PATH. Install FFmpeg 6.1+ and retry." >&2
  exit 1
fi

echo "Generating synthetic fixture: ${OUTPUT}"

ffmpeg -y \
  -f lavfi -i "testsrc=duration=5:size=1280x720:rate=30" \
  -f lavfi -i "sine=frequency=440:duration=5" \
  -c:v libx264 \
  -pix_fmt yuv420p \
  -crf 23 \
  -preset ultrafast \
  -c:a aac \
  -shortest \
  "${OUTPUT}"

echo "Done: ${OUTPUT}"
echo "SHA-256: $(shasum -a 256 "${OUTPUT}" | awk '{print $1}')"
