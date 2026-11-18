# Ogg 重分页工具

对单逻辑流 Ogg(Vorbis)文件重新分页:可调整单页目标大小,保留逻辑流
序号、包边界、granule position 与 BOS/EOS 标志,并重算每页 CRC。

## 文件

- `tools/ogg_repage.py` — 重分页工具(纯 Python Ogg 容器层 + libvorbis
  granule 恢复)
- `tools/vorbis_pcm.py` — libvorbis 的 ctypes 最小绑定,用于恢复每个包
  的真实 granule position 并解码 PCM
- `tools/make_test_vorbis.py` — 生成测试输入 `out/test_input.ogg`
  (libsndfile 编码的合法 Vorbis 文件)
- `tools/make_255_input.py` — 生成 `out/test_input_255.ogg`,其 comment
  头包恰好为 255×17=4335 字节(覆盖"255 整数倍包"与"跨页包"情形)
- `tools/verify_pcm.py` — 用 libsndfile(独立解码器)比较两个文件的 PCM
- `tools/test_edge_cases.py` — 跨页包 / 255 整数倍包的合成流单元测试
- `tools/run_all_checks.py` — 端到端校验(CRC、包流一致、granule、
  首尾标志、页大小上限、PCM 一致)

## 用法

```sh
python3 tools/ogg_repage.py in.ogg out.ogg --target-size 4096 \
    --trace trace.csv
```

- `--target-size N`:单页目标最大字节数(含 27 字节页头与段表;最小
  有效值约 283)。
- `--trace FILE`:输出包级追踪 CSV(包大小、lacing 段数、是否 255 整数
  倍、重分页前后所在页号、是否跨页、granule、EOS)。
- `--granule-mode auto|recover|preserve`:
  - `recover`(Vorbis 默认):用 libvorbis 恢复每个包的真实 granule,
    任意目标大小下每页 granule 均精确;原始各页末尾包的 granule(含
    EOS 裁剪)保持权威。
  - `preserve`:纯容器模式,不依赖 libvorbis;共享同一原始页的包组
    不拆开,要求目标大小能容纳最大包组,否则报错退出。

## 实现要点

- 长度为 255 整数倍的包以 0 终止 lacing 值收尾;若页面恰好被填满,
  该 0 落到下一(continued)页。
- 跨页包:首页之后各页置 continued 标志;无包结束的页 granule 为 -1。
- 每页 granule = 在该页结束的最后一个包的 granule。
- Vorbis 头包分组保持合法:0 号头包独占首页,音频不与末个头包同页。
- CRC 为 Ogg 规定的 CRC-32(多项式 0x04C11DB7,初值 0,不反射)。

## 验证

```sh
python3 tools/run_all_checks.py   # 两个输入 x 四种目标大小
python3 tools/test_edge_cases.py  # 合成流边界测试
```

`run_all_checks.py` 对每份输出校验:全页 CRC 合法、包流逐字节一致、
每页 granule 等于在该页结束的末包的真实 granule、BOS/EOS 标志保留、
页大小不超过目标,并用 libsndfile 独立解码确认 PCM 逐位一致。
