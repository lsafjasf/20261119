#!/bin/bash
# 端到端验证：正常封装 + 两类拒绝场景 + 独立播放器同步验证
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
PASS=0; FAIL=0
ok()   { echo "[PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

echo "===== 1. 正常封装：VP9 视频 + Vorbis 音频（起点不同）====="
if ./scripts/mux_webm.sh -v samples/video_vp9.webm \
     -a samples/audio_vorbis_44100.webm -o out/muxed.webm > work/case1.log 2>&1; then
  ok "封装成功且脚本内置哈希核验通过"
else
  bad "正常用例封装失败"; cat work/case1.log
fi

echo "===== 2. 独立复核：输入/输出码流 SHA-256 逐字节一致 ====="
for spec in "v:0:v:0" "a:0:a:0"; do
  kind="${spec%%:*}"; in_map="${spec#*:}"; 
  in="$([ "$kind" = v ] && echo samples/video_vp9.webm || echo samples/audio_vorbis_44100.webm)"
  h1=$(./bin/ffmpeg -hide_banner -loglevel error -i "$in" -map "$in_map" -c copy -f data - | sha256sum | awk '{print $1}')
  h2=$(./bin/ffmpeg -hide_banner -loglevel error -i out/muxed.webm -map "$in_map" -c copy -f data - | sha256sum | awk '{print $1}')
  [ "$h1" = "$h2" ] && ok "$kind 码流哈希一致 $h1" || bad "$kind 哈希不一致 $h1 != $h2"
done

echo "===== 3. 拒绝场景（均不得产出输出文件）====="
run_reject() { # name expected_substr args...
  local name="$1" pat="$2"; shift 2
  if ./scripts/mux_webm.sh "$@" > work/reject.log 2>&1; then
    bad "$name: 本应拒绝却成功"
  elif grep -q "$pat" work/reject.log; then
    ok "$name: 已阻止 ($pat)"
  else
    bad "$name: 拒绝原因不符"; cat work/reject.log
  fi
}
run_reject "采样率不匹配" "采样率不匹配" \
  -v samples/video_vp9.webm -a samples/audio_vorbis_44100.webm \
  -a samples/audio_opus_48000.webm -o out/x1.webm
run_reject "期望采样率不符" "期望值" \
  -v samples/video_vp9.webm -a samples/audio_vorbis_44100.webm \
  --expect-rate 48000 -o out/x2.webm
run_reject "WebM 不支持的编码" "白名单" \
  -v samples/unsupported_h264_videoonly.mp4 -a samples/audio_vorbis_44100.webm -o out/x3.webm
[ ! -e out/x1.webm ] && [ ! -e out/x2.webm ] && [ ! -e out/x3.webm ] \
  && ok "拒绝时无任何输出文件产生" || bad "发现不应存在的输出文件"

echo "===== 4. 独立播放器 mpv 同步验证（真实 Wayland SHM 渲染）====="
if WAYLAND_DISPLAY=wayland-0 bin/mpv --vo=wlshm --ao=null --no-config --no-terminal \
     --log-file=work/mpv_check.log --script=scripts/sync_probe.lua out/muxed.webm 2>>work/mpv_err.log; then
  n=$(grep -c " SYNC t=" work/mpv_check.log)
  over=$(grep " SYNC t=" work/mpv_check.log | sed -E 's/.*avsync=(-?[0-9.]+).*/\1/' \
          | awk '{ if ($1 < 0) $1 = -$1; if ($1 >= 0.001) bad++ } END { print bad+0 }')
  if [ "$n" -ge 5 ] && [ "$over" = 0 ] && grep -q "total_drops=0" work/mpv_check.log; then
    ok "mpv $n 个采样点 avsync 全部 <1ms 且零丢帧"
  else
    bad "mpv 同步指标异常 (n=$n over_1ms=$over)"; grep SYNC work/mpv_check.log | tail -3
  fi
else
  bad "mpv 播放失败（详见 work/mpv_check.log）"
fi

echo "------------------------------------------"
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
