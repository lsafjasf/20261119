"""重组 PCM 并与项目生成的原始字节逐字节核对。"""

from __future__ import annotations

import os

from .blocks import DATA_SLOTS, PARITY_SLOT, xor_bytes
from .store import ORIGINAL_PCM, load_manifest, scan_bundle


class VerifyResult:
    def __init__(self, ok: bool, message: str, details: dict | None = None):
        self.ok = ok
        self.message = message
        self.details = details or {}


def reassemble(root: str) -> bytes:
    """全部块必须摘要有效；任何缺口/损坏都直接失败，不产出伪造 PCM。"""
    manifest = load_manifest(root)
    report = scan_bundle(root)
    problems = []
    for group_rows in report:
        for row in group_rows:
            if row["state"] != "ok":
                problems.append(f"{row['group']}:{row['slot']}({row['state']})")
    if problems:
        raise RuntimeError(
            "拒绝重组：仍存在不可信块或不自洽校验 -> " + "、".join(problems)
        )
    pieces = []
    for group_rows in report:
        group = group_rows[0]["group"]
        payloads = [
            group_rows[slot]["parsed"].payload for slot in range(DATA_SLOTS)
        ]
        parity = group_rows[PARITY_SLOT]["parsed"].payload
        check = bytes(manifest["block_size"])
        for payload in payloads:
            check = xor_bytes(check, payload)
        if check != parity:
            problems.append(f"{group}:parity(XOR 关系不自洽)")
        pieces.extend(payloads)
    if problems:
        raise RuntimeError(
            "拒绝重组：校验关系不自洽 -> " + "、".join(problems)
        )
    padded = b"".join(pieces)
    return padded[: manifest["pcm_len"]]


def verify_bundle(root: str) -> VerifyResult:
    try:
        recovered = reassemble(root)
    except RuntimeError as exc:
        return VerifyResult(False, str(exc))
    original_path = os.path.join(root, ORIGINAL_PCM)
    if not os.path.exists(original_path):
        return VerifyResult(False, "缺少原始 PCM 基准文件，无法逐字节验证")
    with open(original_path, "rb") as handle:
        original = handle.read()
    manifest = load_manifest(root)
    details = {
        "original_bytes": len(original),
        "recovered_bytes": len(recovered),
        "declared_pcm_len": manifest["pcm_len"],
        "padding_bytes_dropped": len(recovered) - manifest["pcm_len"],
    }
    if len(recovered) != len(original):
        return VerifyResult(False, "长度与原始 PCM 不一致", details)
    if recovered != original:
        first_diff = next(
            i for i, (a, b) in enumerate(zip(recovered, original)) if a != b
        )
        details["first_diff_offset"] = first_diff
        return VerifyResult(False, f"字节比对失败，首个差异位于偏移 {first_diff}", details)
    details["byte_exact_match"] = True
    return VerifyResult(True, "恢复 PCM 与原始 PCM 逐字节完全一致", details)


def write_recovered(root: str, out_name: str = "recovered.pcm") -> str:
    pcm = reassemble(root)
    path = os.path.join(root, out_name)
    with open(path, "wb") as handle:
        handle.write(pcm)
    return path
