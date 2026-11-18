"""命令行入口：inspect（核对）、fields（列可编辑字段）、edit（导出）。"""

from __future__ import annotations

import argparse
import json
import sys

from .chunks import WaveFormatError
from .editor import apply_edits, list_editable_fields
from .inspect import inspect_wave
from .report import render_html, render_text


def _locator_json(locator) -> dict:
    return {
        "chunk": locator.chunk_id,
        "field": locator.field_name,
        "offset": locator.offset,
        "length": locator.length,
        "spec": locator.spec(),
    }


def _cmd_inspect(args: argparse.Namespace) -> int:
    result = inspect_wave(args.file)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as handle:
            handle.write(render_html(result, args.file))
        print(f"HTML 报告已写入 {args.html}")
    if args.json:
        payload = {
            "file": args.file,
            "findings": [
                {
                    "severity": item.severity,
                    "code": item.code,
                    "message": item.message,
                    "locators": [
                        _locator_json(loc) for loc in item.locators
                    ],
                }
                for item in result.findings
            ],
        }
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"JSON 报告已写入 {args.json}")
    if not args.html and not args.json:
        print(render_text(result, args.file))
    return 1 if result.has_errors else 0


def _cmd_fields(args: argparse.Namespace) -> int:
    result = inspect_wave(args.file)
    for field in list_editable_fields(result):
        print(f"{field.key}\t当前值={field.current_value!r}")
    return 0


def _parse_set(items: list[str]) -> dict[str, str]:
    edits: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise argparse.ArgumentTypeError(
                f"--set 需要 key=value 形式，收到 {item!r}"
            )
        key, value = item.split("=", 1)
        edits[key.strip()] = value
    return edits


def _cmd_edit(args: argparse.Namespace) -> int:
    edits = _parse_set(args.set)
    if not edits:
        print("没有提供任何 --set 编辑项", file=sys.stderr)
        return 2
    with open(args.file, "rb") as handle:
        original = handle.read()
    exported = apply_edits(original, edits)
    with open(args.output, "wb") as handle:
        handle.write(exported)

    original_data = inspect_wave(original).wave.data_chunk
    exported_data = inspect_wave(exported).wave.data_chunk
    assert original_data is not None and exported_data is not None
    unchanged = original_data.data == exported_data.data
    print(f"已导出 {args.output}")
    print(f"data chunk 字节完全不变: {unchanged}")
    print(
        f"data chunk: {len(exported_data.data)} 字节，"
        f"偏移 {exported_data.data_offset}"
    )
    return 0 if unchanged else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bwftool",
        description="Broadcast WAV 解析、核对与安全编辑工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_cmd = sub.add_parser("inspect", help="解析并核对 fmt/bext/iXML")
    inspect_cmd.add_argument("file")
    inspect_cmd.add_argument("--html", help="输出单文件 HTML 报告")
    inspect_cmd.add_argument("--json", help="输出 JSON 报告")
    inspect_cmd.set_defaults(func=_cmd_inspect)

    fields_cmd = sub.add_parser(
        "fields", help="列出可编辑字段（只读字段不出现）"
    )
    fields_cmd.add_argument("file")
    fields_cmd.set_defaults(func=_cmd_fields)

    edit_cmd = sub.add_parser("edit", help="只改可编辑字段并导出新 WAV")
    edit_cmd.add_argument("file")
    edit_cmd.add_argument("output")
    edit_cmd.add_argument(
        "--set",
        action="append",
        default=[],
        help="key=value，可重复，例如 --set iXML.TAKE=7",
    )
    edit_cmd.set_defaults(func=_cmd_edit)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except WaveFormatError as exc:
        print(f"文件格式错误: {exc}", file=sys.stderr)
        return 4
    except (ValueError, KeyError) as exc:
        print(f"处理失败: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
