"""一键演示：在独立子目录中分别制造各类故障并展示恢复行为。"""

from __future__ import annotations

import os
import shutil

from . import damage, pcm
from .recover import recover_bundle
from .store import write_bundle
from .verify import verify_bundle


def _fresh_store(root: str) -> str:
    if os.path.exists(root):
        shutil.rmtree(root)
    data = pcm.generate_pcm()
    write_bundle(root, data, block_size=256, sample_rate=8000,
                 wav_path="source.wav")
    return root


def _scene(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def run_demo(base_root: str) -> int:
    scenarios = [
        ("s1_single_missing", _scene_single_missing),
        ("s2_single_corrupt", _scene_single_corrupt),
        ("s3_double_missing", _scene_double_missing),
        ("s4_missing_plus_corrupt", _scene_mixed),
        ("s5_parity_rebuild", _scene_parity),
    ]
    for name, runner in scenarios:
        root = _fresh_store(os.path.join(base_root, name))
        runner(root)
    print("\n演示结束：可恢复场景均逐字节还原；不可恢复场景保留可用块并列出缺口，")
    print("全程没有使用静音/零填充伪造任何数据块。")
    return 0


def _scene_single_missing(root: str) -> None:
    _scene("场景 1：单块缺失 —— 删除组0的 D2，应可由其余四块 XOR 重建")
    damage.delete_block(root, 0, 2)
    report = recover_bundle(root)
    _summarize(report)
    result = verify_bundle(root)
    print("验证:", result.message)


def _scene_single_corrupt(root: str) -> None:
    _scene("场景 2：已有块损坏 —— 翻转组0的 D1 载荷首字节，摘要先报警再重建")
    damage.flip_byte(root, 0, 1, 0)
    report = recover_bundle(root)
    _summarize(report)
    result = verify_bundle(root)
    print("验证:", result.message)


def _scene_double_missing(root: str) -> None:
    _scene("场景 3：双块缺失 —— 同组删除 D0 与 D3，单校验方程无法解两个未知量")
    damage.delete_block(root, 0, 0)
    damage.delete_block(root, 0, 3)
    report = recover_bundle(root)
    _summarize(report)
    result = verify_bundle(root)
    print("验证:", result.message, "（重组被拒绝，缺口保留）")


def _scene_mixed(root: str) -> None:
    _scene("场景 4：缺失 + 损坏混合 —— 删除 D2 且翻转 P，同组两个不可信成员")
    damage.delete_block(root, 0, 2)
    damage.flip_byte(root, 0, 4, 0)
    report = recover_bundle(root)
    _summarize(report)
    result = verify_bundle(root)
    print("验证:", result.message, "（不猜测、不静音补齐）")


def _scene_parity(root: str) -> None:
    _scene("场景 5：校验块自身损坏 —— 翻转 P，数据块完好时直接重算校验块")
    damage.flip_byte(root, 0, 4, 0)
    report = recover_bundle(root)
    _summarize(report)
    result = verify_bundle(root)
    print("验证:", result.message)


def _summarize(report) -> None:
    healthy = 0
    for group in report.groups:
        if group["status"] == "healthy":
            healthy += 1
            continue
        print(f"[组{group['group']}] {group['status']}")
        for entry in group["basis"]:
            print("  依据:", entry)
        for entry in group["actions"]:
            print("  动作:", entry)
        for entry in group["gaps"]:
            print("  缺口:", entry)
    if healthy:
        print(f"其余 {healthy} 个组五块齐全、摘要有效、XOR 自洽（健康）")
    print("总结:", report.summary)
