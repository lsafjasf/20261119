# WebM 视频轨 + 独立音频轨无损重封装

## 目标
把仅含视频的 WebM 与仅含音频的 WebM 重新封装为单个 WebM，**不重新编码任何像素或样本**，
重建容器时间戳并保留各轨起点偏移；采样率不匹配或编码组合不被容器支持时直接拒绝。

## 工具
- `bin/ffmpeg`、`bin/ffprobe`：静态构建（johnvansickle，ffmpeg 7.0.2），仅用 `-c copy` 流拷贝。
- `bin/mpv`：独立播放器（0.41，本地解包依赖，不依赖 ffmpeg 套件），用于 A/V 同步实测。

## 脚本
- `scripts/gen_samples.sh`：生成样本。视频 VP9 起点 1.600s；Vorbis 音频起点 0.297s；
  另含 48kHz Opus（采样率反例）与 H.264 MP4（编码白名单反例）。
- `scripts/mux_webm.sh`：封装工具，见下。
- `scripts/run_all_checks.sh`：端到端 8 项检查，一键复跑。
- `scripts/sync_probe.lua`：mpv 周期采集 `avsync`/jitter/丢帧。

## 封装工具的保证（scripts/mux_webm.sh）
1. **零重编码**：固定 `-c copy`，不存在任何转码分支；像素与样本原样入箱。
2. **编码白名单**：视频仅 `vp8/vp9/av1`、音频仅 `opus/vorbis`（WebM 规范支持组合），
   白名单外（如 H.264/AAC）拒绝，退出码 2，不产出文件。
3. **防静默重采样**：多音频输入采样率必须一致；可用 `--expect-rate` 声明期望采样率，
   不符即拒绝，不会调用任何重采样滤镜。
4. **时间戳重建**：`-copyts -avoid_negative_ts make_non_negative` 保留输入 PTS/DTS，
   仅做满足 Matroska 非负块时间戳的整体平移，轨间相对偏移不变。
5. **封装后自检**：
   - 轨数量 = 1 视频 + N 音频；编码名、采样率与输入逐项比对。
   - 用 `-f data` 导出 packet 载荷计算 SHA-256，输入与输出逐字节一致才算成功。
   - 打印每轨起始偏移、末尾时刻与包数。

## 用法
```bash
./scripts/mux_webm.sh -v samples/video_vp9.webm \
    -a samples/audio_vorbis_44100.webm -o out/muxed.webm
# 多音频（采样率必须相同）：再追加 -a 另一条.webm
# 声明期望采样率：--expect-rate 44100
./scripts/run_all_checks.sh
```

## 本次实测结果（out/muxed.webm）
| 轨 | 编码 | 起始 | 末尾 | 载荷 SHA-256（输入=输出） |
|----|------|------|------|---------------------------|
| 0 视频 | VP9 640x360 yuv420p 25fps | 1.600s | 6.600s | 07767bc6…62ef9 |
| 1 音频 | Vorbis 44100Hz mono | 0.297s | 6.304s | 191d446c…6ba3 |

- 两次封装产物 `cmp` 逐字节一致（bitexact，可重复构建）。
- mpv 以 Wayland SHM 真实渲染窗口完整播放：19 个采样点 `avsync=0.0000`、jitter=0、丢帧 0。
- 拒绝用例：44100/48000 混封、`--expect-rate` 不符、H.264 输入均被阻止且无输出文件。
