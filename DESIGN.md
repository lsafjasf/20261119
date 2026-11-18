# 嵌套序列时间线设计说明

把多条片段时间线（子序列）作为整体放入主时间线（父序列），
支持任意深度嵌套、变速、负偏移、展开与一致性导出。
所有时间用 `fractions.Fraction` 精确表示，避免浮点误差导致的位置不一致。

## 1. 数据模型

- `Clip`：叶子片段，引用一段具体媒体。字段含 `start`、`duration`、
  `source_in`（媒体入点）、`rate`（播放速率）以及画面/音频媒体事件列表。
- `NestedRef`：对另一条序列的嵌套引用。字段含 `start`、`duration`、
  `source_in`、`rate` 与被引用序列名。
- `Sequence`：一条时间线，`clips` 与 `refs` 混合排列。
- `Project`：序列名到序列的容器，提供循环检测、递归渲染。

## 2. 父子时间映射

每个嵌套引用是一条仿射映射（`child` 为子序列局部时间，`parent` 为父序列局部时间）：

```
parent = start + (child - source_in) * rate
child  = source_in + (parent - start) / rate
```

- 二倍速：`rate = 2`。父上占用时长 = 子内容时长 × rate，
  因此子序列 2.5 秒内容在父序列上占 5 秒；单位父时间内播放两倍子内容。
  展开为扁平片段时其速率取倒数（样本中展开片段 `rate = 1/2`）。
- 负偏移：`source_in < 0`。映射仍是同一条连续直线，子序列 0 时刻落在
  `parent = start - source_in*rate > start`，于是 `[start, 该时刻)` 是
  引用的前置手柄区（对应子序列负时间，通常无片段内容）。
- 双层嵌套：逐层复合仿射映射。若内层 `childC = 1 + 2*grandG`、
  外层 `main = 10 + (childC - 1)`，复合后 `main = 10 + 2*grandG`。

## 3. 逐级换算与可见窗口

渲染时维护根坐标系仿射映射 `root = m*local + b`。进入一个嵌套引用时：

```
child_m = m * rate
child_b = m * (start - source_in*rate) + b
```

引用只取用子序列区间 `[source_in, source_out)`，其中
`source_out = source_in + duration/rate`。上层可见窗口是父局部坐标，
进入子序列前必须做逆映射再求交：

```
win_child_lo = source_in + (win_parent_lo - start)/rate
win_child_hi = source_in + (win_parent_hi - start)/rate
```

这样多层嵌套的裁剪不会错位（该逆映射是本实现重点保证的正确性点）。
叶子片段事件先换算到所属序列局部时间，再经当前 `m,b` 换算到根时间，
画面事件与音频事件走同一套映射，因此两者位置始终同步。

## 4. 循环引用拒绝

导出、渲染、展开前统一调用 `Project.ensure_acyclic`。
用 DFS 维护调用栈：引用到栈上已存在的序列即构成环，
返回完整环链（如 `cycX -> cycY -> cycX`）并抛出 `TimelineError`，
导出被拒绝。自引用、缺失序列同样报错，且先于任何递归展开触发。

## 5. 展开嵌套（flatten）

`flatten` 递归遍历主时间线，把每个叶子片段按累积的 `m,b` 换算成
根坐标系下的扁平 `Clip`：

```
flat.start    = m*local_start + b
flat.duration = m*local_duration
flat.rate     = clip.rate / m
flat.source_in= clip.source_in + (local_start - clip.start)*clip.rate
```

被上层窗口裁掉的部分会生成修剪后的片段（调整 `source_in` 与时长）。
展开是深拷贝式的：扁平片段携带独立的事件列表副本，与所有子序列
不再共享状态，之后可独立修改，互不影响。

## 6. 导出一致性

`export_timeline` 先拒绝循环引用，再分别渲染嵌套形态与展开形态，
按 `(完整路径名, 事件类型, 根时间, 源媒体时间)` 逐事件比对。
两套结果完全相等才导出，并置 `consistency_checked=True`；
任何不一致都会抛出异常而不是静默产出错误结果。
画面与音频事件使用同一映射，故展开前后两者的根时间位置逐一相等。

## 7. 编辑语义

- 父序列编辑（移动/变速/改偏移）只改 `NestedRef` 的映射参数，
  渲染结果随之整体变化，但子序列内部的片段与结构完全不动。
- 同一子序列可被多条引用复用，每处引用拥有独立的映射参数。
- 展开后的序列是独立副本，对它的修改不会回写到任何子序列。

## 8. 样本与验证

- `nested_timeline/samples.py`：二倍速、负偏移、双层嵌套、循环引用四组样本。
- `demo.py`：逐级父子换算演示、渲染事件、展开对比、循环拒绝、编辑传播。
- `tests/`：15 个单元测试覆盖双向换算、复合映射、窗口裁剪、
  循环/自引用/缺失序列、展开前后音视频位置一致性、编辑独立性。

运行方式：

```
python3 demo.py
python3 -m unittest discover -s tests -v
```
