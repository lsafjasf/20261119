"""XOR 单校验恢复器。

恢复依据（GF(2) 字节域，加法即 XOR，x ^ x == 0）：
设校验块 P = D0 ^ D1 ^ D2 ^ D3，则任一成员 X 都可由其余四块 XOR 得到，
例如 D2 = D0 ^ D1 ^ D3 ^ P。因此每组最多容忍 1 个块缺失或损坏；
缺失与损坏是两种不同故障，恢复器分别处理并在报告中注明依据。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .blocks import (
    DATA_SLOTS,
    PARITY_SLOT,
    SLOT_COUNT,
    build_block,
    digest_payload,
    xor_bytes,
)
from .store import block_path, load_manifest, scan_block


@dataclass
class GroupReport:
    group: int
    status: str  # healthy | repaired | unrecoverable | sanity_failed
    slots: dict
    actions: list[str] = field(default_factory=list)
    basis: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


@dataclass
class RecoveryReport:
    ok: bool
    summary: str
    groups: list[dict]
    missing_slots: list[str]
    corrupt_slots: list[str]
    repaired_slots: list[str]
    unrecovered_gaps: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _manifest_digest(manifest: dict, group: int, slot: int) -> bytes:
    return bytes.fromhex(manifest["block_digests"][f"{group}:{slot}"])


def _rebuild(bad_slot: int, good: dict) -> tuple[bytes, str]:
    acc = bytes(len(next(iter(good.values()))["parsed"].payload))
    members = " ^ ".join(
        ("P" if slot == PARITY_SLOT else f"D{slot}")
        for slot in range(SLOT_COUNT) if slot != bad_slot
    )
    for slot in range(SLOT_COUNT):
        if slot == bad_slot:
            continue
        acc = xor_bytes(acc, good[slot]["parsed"].payload)
    target = "P" if bad_slot == PARITY_SLOT else f"D{bad_slot}"
    return acc, f"{target} = {members}（XOR 自反性：x^x=0，其余成员两两消去）"


def recover_bundle(root: str) -> RecoveryReport:
    manifest = load_manifest(root)
    group_reports: list[GroupReport] = []
    missing: list[str] = []
    corrupt: list[str] = []
    repaired: list[str] = []
    gaps: list[str] = []

    for group in range(manifest["group_count"]):
        scans = [scan_block(root, group, slot) for slot in range(SLOT_COUNT)]
        slot_states = {s["slot"]: s["state"] for s in scans}
        missing_here = [s["slot"] for s in scans if s["state"] == "missing"]
        corrupt_here = [s["slot"] for s in scans if s["state"] == "corrupt"]
        missing += [f"{group}:{s}" for s in missing_here]
        corrupt += [f"{group}:{s}" for s in corrupt_here]
        report = GroupReport(group=group, status="healthy", slots=slot_states)
        good = {s["slot"]: s for s in scans if s["state"] == "ok"}

        if not missing_here and not corrupt_here:
            parity_calc = _xor_data_payloads(good)
            if parity_calc != good[PARITY_SLOT]["parsed"].payload:
                report.status = "sanity_failed"
                report.basis.append("四块数据齐全但 P != D0^D1^D2^D3，摘要通过也说明内部不自洽")
                report.gaps.append(f"{group}:{PARITY_SLOT}(校验关系异常)")
                gaps.append(f"{group}:{PARITY_SLOT}(校验关系异常)")
            else:
                report.basis.append("五块摘要全部有效且 P == D0^D1^D2^D3，组健康")
            group_reports.append(report)
            continue

        bad_count = len(missing_here) + len(corrupt_here)
        kinds = []
        if len(missing_here) == 1:
            kinds.append(f"单块缺失（{_label(missing_here[0])}）")
        elif len(missing_here) >= 2:
            kinds.append(f"{len(missing_here)} 块缺失（{_labels(missing_here)}）")
        if corrupt_here:
            kinds.append(f"{len(corrupt_here)} 块损坏（{_labels(corrupt_here)}）")

        if bad_count >= 2:
            report.status = "unrecoverable"
            report.basis.append(
                "故障类型：" + "，".join(kinds)
                + "。单校验组只有 1 个独立线性方程，任意 2 个未知量都无解"
            )
            report.actions.append("保留全部尚存且摘要有效的块，不写入任何猜测数据")
            for slot in missing_here:
                report.gaps.append(f"{group}:{slot}(缺失)")
                gaps.append(f"{group}:{slot}(缺失)")
            for slot in corrupt_here:
                report.gaps.append(f"{group}:{slot}(损坏且无法信任)")
                gaps.append(f"{group}:{slot}(损坏且无法信任)")
            group_reports.append(report)
            continue

        bad_slot = (missing_here + corrupt_here)[0]
        if missing_here:
            report.basis.append("故障类型：单块缺失。块文件不在磁盘上，需要完整重建")
        else:
            report.basis.append(
                f"故障类型：已有块损坏。{_label(bad_slot)} 文件仍在，"
                "但其 SHA-256 摘要不匹配，内容不可信任，按未知量处理后重建"
            )
        rebuilt, equation = _rebuild(bad_slot, good)
        report.basis.append("恢复依据：" + equation)

        expected = _manifest_digest(manifest, group, bad_slot)
        actual = digest_payload(group, bad_slot, rebuilt)
        if actual != expected:
            report.status = "sanity_failed"
            report.basis.append("重建结果与清单登记摘要不一致，拒绝写入，避免传播错误数据")
            report.gaps.append(f"{group}:{bad_slot}(重建摘要不符)")
            gaps.append(f"{group}:{bad_slot}(重建摘要不符)")
            group_reports.append(report)
            continue

        with open(block_path(root, group, bad_slot), "wb") as handle:
            handle.write(build_block(group, bad_slot, rebuilt))
        repaired.append(f"{group}:{bad_slot}")
        report.status = "repaired"
        report.slots[bad_slot] = "repaired"
        report.actions.append(
            f"已重建并写回 {_label(bad_slot)}，重建摘要与清单登记一致"
        )
        group_reports.append(report)

    ok = not gaps
    if ok:
        summary = "全部组恢复完成，所有块摘要有效且校验关系自洽"
    else:
        summary = f"存在 {len(gaps)} 处无法恢复的缺口，已保留可用块并如实列出，未做静音/零填充冒充"
    return RecoveryReport(
        ok=ok,
        summary=summary,
        groups=[asdict(r) for r in group_reports],
        missing_slots=missing,
        corrupt_slots=corrupt,
        repaired_slots=repaired,
        unrecovered_gaps=gaps,
    )


def _xor_data_payloads(good: dict) -> bytes:
    length = len(next(iter(good.values()))["parsed"].payload)
    acc = bytes(length)
    for slot in range(DATA_SLOTS):
        acc = xor_bytes(acc, good[slot]["parsed"].payload)
    return acc


def _label(slot: int) -> str:
    return "P(校验块)" if slot == PARITY_SLOT else f"D{slot}(数据块)"


def _labels(slots: list[int]) -> str:
    return "、".join(_label(slot) for slot in slots)
