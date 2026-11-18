"""磁盘存储：清单、分块打包与状态扫描。"""

from __future__ import annotations

import json
import os

from .blocks import (
    DATA_SLOTS,
    HEADER_SIZE,
    PARITY_SLOT,
    SLOT_COUNT,
    build_block,
    digest_payload,
    parse_block,
    parity_of,
)

MANIFEST_NAME = "manifest.json"
ORIGINAL_PCM = "source.pcm"
ORIGINAL_WAV = "source.wav"


def block_filename(group: int, slot: int) -> str:
    kind = "parity" if slot == PARITY_SLOT else f"data{slot}"
    return f"g{group:04d}_{kind}.block"


def manifest_path(root: str) -> str:
    return os.path.join(root, MANIFEST_NAME)


def block_path(root: str, group: int, slot: int) -> str:
    return os.path.join(root, block_filename(group, slot))


def chunk_data(pcm: bytes, block_size: int) -> list[list[bytes]]:
    """按 block_size 切分，每 DATA_SLOTS 块一组，末组以零填充到等长。

    填充是显式的：清单记录原始 PCM 长度，重组后按长度截断，
    恢复链路永远不会把填充或静音冒充成真实数据。
    """
    total_blocks = (len(pcm) + block_size - 1) // block_size
    group_count = max(1, (total_blocks + DATA_SLOTS - 1) // DATA_SLOTS)
    groups: list[list[bytes]] = []
    for group in range(group_count):
        payloads = []
        for slot in range(DATA_SLOTS):
            index = group * DATA_SLOTS + slot
            start = index * block_size
            piece = pcm[start:start + block_size]
            if len(piece) < block_size:
                piece = piece + b"\x00" * (block_size - len(piece))
            payloads.append(piece)
        groups.append(payloads)
    return groups


def write_bundle(root: str, pcm: bytes, block_size: int,
                 sample_rate: int, wav_path: str | None = None) -> dict:
    os.makedirs(root, exist_ok=True)
    groups = chunk_data(pcm, block_size)
    block_digests: dict[str, str] = {}
    for group, payloads in enumerate(groups):
        for slot, payload in enumerate(payloads):
            blob = build_block(group, slot, payload)
            with open(block_path(root, group, slot), "wb") as handle:
                handle.write(blob)
            block_digests[f"{group}:{slot}"] = digest_payload(group, slot, payload).hex()
        parity = parity_of(payloads)
        with open(block_path(root, group, PARITY_SLOT), "wb") as handle:
            handle.write(build_block(group, PARITY_SLOT, parity))
        block_digests[f"{group}:{PARITY_SLOT}"] = digest_payload(
            group, PARITY_SLOT, parity
        ).hex()
    manifest = {
        "sample_rate": sample_rate,
        "channels": 1,
        "sample_width": 2,
        "block_size": block_size,
        "group_count": len(groups),
        "data_slots": DATA_SLOTS,
        "parity_slot": PARITY_SLOT,
        "pcm_len": len(pcm),
        "padded_len": len(groups) * DATA_SLOTS * block_size,
        "block_digests": block_digests,
    }
    with open(manifest_path(root), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    with open(os.path.join(root, ORIGINAL_PCM), "wb") as handle:
        handle.write(pcm)
    if wav_path:
        from .pcm import write_wav
        write_wav(os.path.join(root, wav_path), pcm, sample_rate)
    return manifest


def load_manifest(root: str) -> dict:
    with open(manifest_path(root), encoding="utf-8") as handle:
        return json.load(handle)


def scan_block(root: str, group: int, slot: int) -> dict:
    """返回单个槽位状态：ok / missing / corrupt，附解析细节。"""
    path = block_path(root, group, slot)
    if not os.path.exists(path):
        return {"group": group, "slot": slot, "state": "missing",
                "reason": "块文件不存在（被删除）", "parsed": None}
    with open(path, "rb") as handle:
        blob = handle.read()
    parsed = parse_block(blob)
    state = "ok" if parsed.valid else "corrupt"
    return {"group": group, "slot": slot, "state": state,
            "reason": parsed.reason, "parsed": parsed}


def scan_bundle(root: str) -> list[list[dict]]:
    manifest = load_manifest(root)
    report = []
    for group in range(manifest["group_count"]):
        report.append([
            scan_block(root, group, slot) for slot in range(SLOT_COUNT)
        ])
    return report


def expected_payload_len(manifest: dict, group: int, slot: int) -> int:
    """所有块等长；末组同样填充到 block_size。"""
    if slot >= SLOT_COUNT:
        raise ValueError("非法槽位")
    return manifest["block_size"]


def header_overhead() -> int:
    return HEADER_SIZE
