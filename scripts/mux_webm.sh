#!/bin/bash
# mux_webm.sh - 仅重新封装(remux) WebM：独立视频轨 + 独立音频轨 -> 单个 WebM
# 严格约束：
#   1. 只允许流拷贝(-c copy)，任何情况下都不重新编码像素或样本
#   2. 重建容器内时间戳，保留各轨原始起点偏移(-copyts)
#   3. 仅允许 WebM 容器支持的编码组合，白名单外一律拒绝
#   4. 采样率不匹配（多音频轨之间，或与显式 --expect-rate 不一致）时阻止封装，
#      绝不静默重采样
#   5. 封装后自动核验：轨数量、编码配置、码流载荷 SHA-256 与输入完全一致
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FFPROBE="$HERE/bin/ffprobe"
FFMPEG="$HERE/bin/ffmpeg"

VIDEO_WHITELIST="vp8 vp9 av1"
AUDIO_WHITELIST="opus vorbis"

usage() {
  cat <<USAGE
用法: $(basename "$0") -v 视频.webm -a 音频.webm [-a 音频2.webm ...] -o 输出.webm [--expect-rate Hz]
  -v, --video FILE      仅含视频轨的输入文件（恰好 1 条视频轨）
  -a, --audio FILE      仅含音频轨的输入文件（可重复；每个文件恰好 1 条音频轨）
  -o, --output FILE     输出 WebM 路径
      --expect-rate Hz  可选；声明期望音频采样率，不符则拒绝
  -h, --help            显示帮助
退出码: 0 成功; 2 参数/预检失败(拒绝封装); 1 其他错误
USAGE
}

die() { echo "错误: $*" >&2; exit 2; }

VIDEO_IN=""; OUT=""; EXPECT_RATE=""
AUDIO_INS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -v|--video) VIDEO_IN="$2"; shift 2;;
    -a|--audio) AUDIO_INS+=("$2"); shift 2;;
    -o|--output) OUT="$2"; shift 2;;
    --expect-rate) EXPECT_RATE="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) die "未知参数: $1";;
  esac
done

[ -n "$VIDEO_IN" ] || die "缺少 -v 视频输入"
[ "${#AUDIO_INS[@]}" -gt 0 ] || die "缺少至少一个 -a 音频输入"
[ -n "$OUT" ] || die "缺少 -o 输出路径"
[ -f "$VIDEO_IN" ] || die "视频文件不存在: $VIDEO_IN"
for a in "${AUDIO_INS[@]}"; do [ -f "$a" ] || die "音频文件不存在: $a"; done
case "$OUT" in *.webm) ;; *) die "输出必须是 .webm 容器: $OUT";; esac

probe() { # file -> ffprobe JSON
  "$FFPROBE" -v error -print_format json -show_streams -show_format "$1"
}

json_get() { # file stream_index field
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
meta = json.load(open(sys.argv[1]))
idx, field = int(sys.argv[2]), sys.argv[3]
for st in meta["streams"]:
    if st["index"] == idx:
        print(st.get(field, ""))
        break
PY
}

# ---------- 预检 1：容器与轨数量 ----------
VJSON="$(probe "$VIDEO_IN")"
V_NV=$(echo "$VJSON" | python3 -c 'import json,sys;print(sum(1 for s in json.load(sys.stdin)["streams"] if s["codec_type"]=="video"))')
V_NA=$(echo "$VJSON" | python3 -c 'import json,sys;print(sum(1 for s in json.load(sys.stdin)["streams"] if s["codec_type"]=="audio"))')
[ "$V_NV" = "1" ] || die "视频输入必须恰好 1 条视频轨（实际 $V_NV 条）: $VIDEO_IN"
[ "$V_NA" = "0" ] || die "视频输入不得包含音频轨（实际 $V_NA 条）: $VIDEO_IN"

VIDX=$(echo "$VJSON" | python3 -c 'import json,sys;print([s["index"] for s in json.load(sys.stdin)["streams"] if s["codec_type"]=="video"][0])')
VCODEC=$(json_get <(echo "$VJSON") "$VIDX" codec_name)
VFMT=$(echo "$VJSON" | python3 -c 'import json,sys;print(json.load(sys.stdin)["format"]["format_name"])')
case " $VIDEO_WHITELIST " in *" $VCODEC "*) ;;
  *) die "视频编码 $VCODEC 不在 WebM 白名单($VIDEO_WHITELIST)内，拒绝封装且不会转码: $VIDEO_IN";;
esac
case "$VFMT" in *webm*|*matroska*) ;;
  *) die "视频输入容器 $VFMT 非 WebM/Matroska: $VIDEO_IN";;
esac

AJSONS=(); ACODECS=(); ARATES=(); AIDXS=()
for a in "${AUDIO_INS[@]}"; do
  j="$(probe "$a")"; AJSONS+=("$j")
  nv=$(echo "$j" | python3 -c 'import json,sys;print(sum(1 for s in json.load(sys.stdin)["streams"] if s["codec_type"]=="video"))')
  na=$(echo "$j" | python3 -c 'import json,sys;print(sum(1 for s in json.load(sys.stdin)["streams"] if s["codec_type"]=="audio"))')
  [ "$nv" = "0" ] || die "音频输入不得包含视频轨: $a"
  [ "$na" = "1" ] || die "每个音频输入必须恰好 1 条音频轨（实际 $na 条）: $a"
  aidx=$(echo "$j" | python3 -c 'import json,sys;print([s["index"] for s in json.load(sys.stdin)["streams"] if s["codec_type"]=="audio"][0])')
  ac=$(json_get <(echo "$j") "$aidx" codec_name)
  ar=$(json_get <(echo "$j") "$aidx" sample_rate)
  afmt=$(echo "$j" | python3 -c 'import json,sys;print(json.load(sys.stdin)["format"]["format_name"])')
  case " $AUDIO_WHITELIST " in *" $ac "*) ;;
    *) die "音频编码 $ac 不在 WebM 白名单($AUDIO_WHITELIST)内，拒绝封装且不会转码: $a";;
  esac
  case "$afmt" in *webm*|*matroska*) ;;
    *) die "音频输入容器 $afmt 非 WebM/Matroska: $a";;
  esac
  ACODECS+=("$ac"); ARATES+=("$ar"); AIDXS+=("$aidx")
done

# ---------- 预检 2：采样率防重采样拦截 ----------
REF_RATE="${ARATES[0]}"
for i in "${!ARATES[@]}"; do
  [ "${ARATES[$i]}" = "$REF_RATE" ] || \
    die "采样率不匹配: ${AUDIO_INS[0]}=${REF_RATE}Hz vs ${AUDIO_INS[$i]}=${ARATES[$i]}Hz；已阻止封装，不会静默重采样"
done
if [ -n "$EXPECT_RATE" ]; then
  [ "$REF_RATE" = "$EXPECT_RATE" ] || \
    die "采样率 $REF_RATE 与期望值 $EXPECT_RATE 不符；已阻止封装，不会静默重采样"
fi

echo "预检通过: 视频 $VCODEC / 音频 ${ACODECS[*]} @ ${REF_RATE}Hz / 音频轨 ${#AUDIO_INS[@]} 条"

# ---------- 封装：纯流拷贝，重建容器时间戳并保留轨间偏移 ----------
TMP_OUT="$OUT.tmp.$$.webm"
trap 'rm -f "$TMP_OUT"' EXIT
ARGS=(-hide_banner -loglevel error -y -i "$VIDEO_IN")
for a in "${AUDIO_INS[@]}"; do ARGS+=(-i "$a"); done
MAPS=(-map 0:v:0)
for i in "${!AUDIO_INS[@]}"; do MAPS+=(-map "$((i+1)):a:0"); done
# -c copy            像素/样本原样复制，零重编码
# -copyts            保留输入 PTS/DTS，使各轨起点偏移进入新容器
# avoid_negative_ts  Matroska 块时间戳需非负，仅做整体平移，不改轨间相对关系
# bitexact           不写入版本类随机元数据，保证可重复构建
"$FFMPEG" "${ARGS[@]}" "${MAPS[@]}" -c copy -copyts \
  -avoid_negative_ts make_non_negative -fflags +bitexact \
  -map_metadata -1 "$TMP_OUT"

# ---------- 核验 1：轨数量与编码配置 ----------
OJSON="$(probe "$TMP_OUT")"
exp_streams=$((1 + ${#AUDIO_INS[@]}))
got_streams=$(echo "$OJSON" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["streams"]))')
[ "$got_streams" = "$exp_streams" ] || die "轨数量不符: 期望 $exp_streams 实际 $got_streams"
ov=$(echo "$OJSON" | python3 -c 'import json,sys;print([s for s in json.load(sys.stdin)["streams"] if s["codec_type"]=="video"][0]["codec_name"])')
[ "$ov" = "$VCODEC" ] || die "输出视频编码被改变: $ov != $VCODEC"
mapfile -t oa < <(echo "$OJSON" | python3 -c 'import json,sys;[print(s["codec_name"],s["sample_rate"]) for s in json.load(sys.stdin)["streams"] if s["codec_type"]=="audio"]')
for i in "${!ACODECS[@]}"; do
  got="${oa[$i]}"
  [ "$got" = "${ACODECS[$i]} ${ARATES[$i]}" ] || die "输出音频轨 $i 编码/采样率被改变: '$got' != '${ACODECS[$i]} ${ARATES[$i]}'"
done

# ---------- 核验 2：码流载荷逐字节哈希（data 封装只写 packet 载荷）----------
payload_hash() { # file stream_spec
  "$FFMPEG" -hide_banner -loglevel error -i "$1" -map "$2" -c copy -f data - 2>/dev/null | sha256sum | awk '{print $1}'
}
hv_in=$(payload_hash "$VIDEO_IN" "0:v:0"); hv_out=$(payload_hash "$TMP_OUT" "0:v:0")
[ "$hv_in" = "$hv_out" ] || die "视频码流哈希不一致: $hv_in != $hv_out"
for i in "${!AUDIO_INS[@]}"; do
  ha_in=$(payload_hash "${AUDIO_INS[$i]}" "0:a:0")
  ha_out=$(payload_hash "$TMP_OUT" "0:a:$i")
  [ "$ha_in" = "$ha_out" ] || die "音频轨 $i 码流哈希不一致: $ha_in != $ha_out"
done

# ---------- 核验 3：时间戳（每轨起始偏移 / 末尾时刻）----------
echo "--- 每轨时间戳（容器时间轴，单位秒）---"
FFPROBE_BIN="$FFPROBE" python3 - "$TMP_OUT" <<'PY'
import json, subprocess, sys
f = sys.argv[1]
ffp = __import__("os").environ["FFPROBE_BIN"]
meta = json.loads(subprocess.run([ffp,"-v","error","-print_format","json",
    "-show_streams","-show_packets",f],capture_output=True,text=True).stdout)
for st in meta["streams"]:
    pts = [float(p["pts_time"]) for p in meta["packets"] if p["stream_index"]==st["index"] and "pts_time" in p]
    dur = [float(p["duration_time"]) for p in meta["packets"] if p["stream_index"]==st["index"] and "duration_time" in p]
    end = (pts[-1] + dur[-1]) if dur else pts[-1]
    kind = st["codec_type"]
    tag = f"{kind}:{st['codec_name']}"
    if kind=="audio": tag += f"@{st.get('sample_rate')}Hz"
    print(f"  轨{st['index']} {tag:18s} 起始={pts[0]:.3f}s  末尾={end:.3f}s  包数={len(pts)}")
PY
echo "哈希核验通过: 视频 $hv_in"
for i in "${!AUDIO_INS[@]}"; do
  echo "哈希核验通过: 音频轨$i $(payload_hash "${AUDIO_INS[$i]}" 0:a:0)"
done
mv "$TMP_OUT" "$OUT"; trap - EXIT
echo "封装完成（零重编码）: $OUT"
