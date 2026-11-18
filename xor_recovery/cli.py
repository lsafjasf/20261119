"""命令行入口：init/status/delete/flip/recover/verify/demo。"""

from __future__ import annotations

import argparse
import json
import sys

from . import damage, demo, pcm
from .recover import recover_bundle
from .store import load_manifest, scan_bundle, write_bundle
from .verify import verify_bundle, write_recovered


def _print_scan(root: str) -> None:
    manifest = load_manifest(root)
    print(f"组数量={manifest['group_count']} 块大小={manifest['block_size']} "
          f"PCM 原始长度={manifest['pcm_len']} 填充={manifest['padded_len'] - manifest['pcm_len']}B")
    for group_rows in scan_bundle(root):
        line = " ".join(
            f"{('P' if r['slot'] == 4 else 'D' + str(r['slot']))}:{r['state']}"
            for r in group_rows
        )
        print(f"  组{group_rows[0]['group']:>2}: {line}")


def _print_report(report) -> None:
    for group in report.groups:
        print(f"[组{group['group']}] 状态={group['status']}")
        for entry in group["basis"]:
            print(f"    依据: {entry}")
        for entry in group["actions"]:
            print(f"    动作: {entry}")
        for entry in group["gaps"]:
            print(f"    缺口: {entry}")
    print(f"总结: {report.summary}")
    if report.repaired_slots:
        print("已重建: " + ", ".join(report.repaired_slots))
    if report.unrecovered_gaps:
        print("未恢复缺口: " + ", ".join(report.unrecovered_gaps))


def cmd_init(args) -> int:
    data = pcm.generate_pcm(args.seconds, args.sample_rate)
    write_bundle(args.root, data, args.block_size, args.sample_rate,
                 wav_path="source.wav")
    print(f"已生成 {len(data)} 字节 PCM 并完成 4+1 分块，目录: {args.root}")
    _print_scan(args.root)
    return 0


def cmd_status(args) -> int:
    _print_scan(args.root)
    return 0


def cmd_delete(args) -> int:
    path = damage.delete_block(args.root, args.group, args.slot)
    print(f"已删除块 {args.group}:{args.slot} -> {path}")
    return 0


def cmd_flip(args) -> int:
    target = damage.flip_byte(args.root, args.group, args.slot,
                              args.offset, in_payload=not args.raw_offset)
    scope = "文件绝对" if args.raw_offset else "载荷相对"
    print(f"已翻转块 {args.group}:{args.slot} 的{scope}字节 {target}（XOR 0xFF）")
    return 0


def cmd_recover(args) -> int:
    report = recover_bundle(args.root)
    _print_report(report)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 2


def cmd_verify(args) -> int:
    result = verify_bundle(args.root)
    print(result.message)
    for key, value in result.details.items():
        print(f"  {key}: {value}")
    if result.ok:
        path = write_recovered(args.root)
        print(f"已写出重组文件: {path}")
        return 0
    print("未写出任何重组文件：不能用静音或猜测数据冒充恢复结果")
    return 2


def cmd_demo(args) -> int:
    return demo.run_demo(args.root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xor_recovery",
        description="分块媒体 XOR(4+1) 擦除恢复演示：单块可重建，双块缺失如实报缺口",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="生成 PCM 并建立 4+1 分块")
    p_init.add_argument("--root", default="media_store")
    p_init.add_argument("--block-size", type=int, default=256)
    p_init.add_argument("--sample-rate", type=int, default=8000)
    p_init.add_argument("--seconds", type=float, default=2.5)
    p_init.set_defaults(func=cmd_init)

    p_status = sub.add_parser("status", help="扫描并打印各组块状态")
    p_status.add_argument("--root", default="media_store")
    p_status.set_defaults(func=cmd_status)

    p_del = sub.add_parser("delete", help="按位置删除一个块（模拟缺失）")
    p_del.add_argument("--root", default="media_store")
    p_del.add_argument("--group", type=int, required=True)
    p_del.add_argument("--slot", type=int, required=True, choices=range(5))
    p_del.set_defaults(func=cmd_delete)

    p_flip = sub.add_parser("flip", help="翻转块内一个字节（模拟损坏）")
    p_flip.add_argument("--root", default="media_store")
    p_flip.add_argument("--group", type=int, required=True)
    p_flip.add_argument("--slot", type=int, required=True, choices=range(5))
    p_flip.add_argument("--offset", type=int, required=True)
    p_flip.add_argument("--raw-offset", action="store_true",
                        help="offset 为块文件绝对偏移，默认是载荷相对偏移")
    p_flip.set_defaults(func=cmd_flip)

    p_rec = sub.add_parser("recover", help="执行恢复并打印依据/缺口")
    p_rec.add_argument("--root", default="media_store")
    p_rec.add_argument("--json", action="store_true")
    p_rec.set_defaults(func=cmd_recover)

    p_ver = sub.add_parser("verify", help="重组并与原始 PCM 逐字节验证")
    p_ver.add_argument("--root", default="media_store")
    p_ver.set_defaults(func=cmd_verify)

    p_demo = sub.add_parser("demo", help="一键跑全部故障场景演示")
    p_demo.add_argument("--root", default="demo_store")
    p_demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
