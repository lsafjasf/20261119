# calibtool — 运动相机镜头畸变标定与去畸变

基于棋盘格的相机内参 / 径向畸变标定工具，面向运动相机（广角、强桶形畸变）素材。

## 安装

```bash
pip install -r requirements.txt   # numpy, opencv-python-headless
```

## 功能

- `generate`：用已知内参和畸变系数渲染一组棋盘格投影图（含 `ground_truth.json`），
  作为可核验的标定输入；`--degenerate` 生成近乎相同姿态的不足约束样本。
- `calibrate`：检测角点、拟合内参 + 径向/切向畸变（默认 k1,k2,p1,p2,k3，
  `--rational` 可切 8 系数模型）。逐图显示重投影误差与去畸变后网格直线偏离；
  `--exclude i j ...` 剔除劣质观测后重新拟合；若图像目录中存在
  `ground_truth.json`，自动与真值对比。
- `undistort`：用标定结果批量去畸变，并报告每张图的网格直线度。

## 不足约束保护

当可用视角过少、棋盘法向散布 < 10° 或距离变化不足时，工具判定输入约束不足，
打印 `UNRELIABLE CALIBRATION` 警告并拒绝输出看似精确的参数（退出码 3，
不写参数文件）；`--force` 可查看不可信的原始拟合值。

## 示例

```bash
python3 -m calibtool generate data/synth --count 12
python3 -m calibtool calibrate data/synth --out params.json
python3 -m calibtool calibrate data/synth --exclude 3 7 --out params.json
python3 -m calibtool undistort data/synth --params params.json \
    --out-dir data/undistorted --pattern 9x6
python3 -m calibtool generate data/degen --degenerate
python3 -m calibtool calibrate data/degen   # 应被拒绝
```

本地图像：把棋盘格照片放入任意目录，替换上面的 `data/synth` 即可，
用 `--pattern 9x6 --square 0.025` 匹配实际棋盘（内角点数 x 方格边长，单位米）。
