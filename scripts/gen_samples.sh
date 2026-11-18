#!/bin/bash
# 生成测试样本：独立 WebM 视频轨 + 独立音频轨（起点不同），以及两组负例素材
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
FF="$HERE/bin/ffmpeg"
S="$HERE/samples"; W="$HERE/work"
mkdir -p "$S" "$W"

# 1) 视频-only VP9，5s，25fps；起点设为 +1.6s（容器内时间轴）
$FF -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=640x360:rate=25:duration=5" \
  -c:v libvpx-vp9 -b:v 600k -g 25 -keyint_min 25 \
  -output_ts_offset 1.6 \
  "$S/video_vp9.webm"

# 2) 音频-only Vorbis @44.1kHz，6s；起点设为 +0.30s
$FF -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=440:sample_rate=44100:duration=6" \
  -c:a libvorbis -q:a 4 \
  -output_ts_offset 0.30 \
  "$S/audio_vorbis_44100.webm"

# 3) 负例：Opus @48kHz（与 44.1kHz 输入采样率不同 -> 脚本必须拒绝，不得重采样）
$FF -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=880:sample_rate=48000:duration=4" \
  -c:a libopus -b:a 64k \
  "$S/audio_opus_48000.webm"

# 4) 负例：WebM 不支持的编码组合（H.264 + AAC，MP4 容器）-> 脚本必须拒绝
$FF -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=320x240:rate=15:duration=2" \
  -f lavfi -i "sine=frequency=300:sample_rate=48000:duration=2" \
  -c:v libx264 -preset ultrafast -c:a aac -shortest \
  "$S/unsupported_h264_aac.mp4"

echo "samples generated:"
ls -la "$S"
