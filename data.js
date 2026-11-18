// 视频访问单元（帧）数据集。
// 每帧字段:
//   poc       显示顺序 (Picture Order Count)
//   type      I / P / B
//   decodeIdx 码流中的实际解码顺序（0 起）
//   refs      参考帧列表，field 为引用来源字段（RefPicList0/1，即 L0/L1 双向预测）
//   isIDR     是否 IDR（无参考的合法起点）
const DATASETS = {
  // 正常短 GOP：IBBPBBPBB + 下一 GOP 的 I 帧，B 帧双向参考
  normal: {
    name: "正常 GOP (IBBPBBPBB, 双向参考)",
    frames: [
      { poc: 0, type: "I", decodeIdx: 0, isIDR: true, refs: [] },
      { poc: 1, type: "B", decodeIdx: 3, refs: [
        { poc: 0, field: "RefPicList0[0]" },
        { poc: 3, field: "RefPicList1[0]" } ] },
      { poc: 2, type: "B", decodeIdx: 4, refs: [
        { poc: 0, field: "RefPicList0[0]" },
        { poc: 3, field: "RefPicList1[0]" } ] },
      { poc: 3, type: "P", decodeIdx: 1, refs: [
        { poc: 0, field: "RefPicList0[0]" } ] },
      { poc: 4, type: "B", decodeIdx: 6, refs: [
        { poc: 3, field: "RefPicList0[0]" },
        { poc: 6, field: "RefPicList1[0]" } ] },
      { poc: 5, type: "B", decodeIdx: 7, refs: [
        { poc: 3, field: "RefPicList0[0]" },
        { poc: 6, field: "RefPicList1[0]" } ] },
      { poc: 6, type: "P", decodeIdx: 2, refs: [
        { poc: 3, field: "RefPicList0[0]" } ] },
      { poc: 7, type: "B", decodeIdx: 9, refs: [
        { poc: 6, field: "RefPicList0[0]" },
        { poc: 9, field: "RefPicList1[0]" } ] },
      { poc: 8, type: "B", decodeIdx: 10, refs: [
        { poc: 6, field: "RefPicList0[0]" },
        { poc: 9, field: "RefPicList1[0]" } ] },
      { poc: 9, type: "I", decodeIdx: 5, isIDR: true, refs: [] },
    ],
  },
  // 异常 GOP：含依赖环、孤儿帧、无法解析的引用
  broken: {
    name: "异常 GOP (含环 / 孤儿帧 / 悬空引用)",
    frames: [
      { poc: 0, type: "I", decodeIdx: 0, isIDR: true, refs: [] },
      { poc: 1, type: "B", decodeIdx: 3, refs: [
        { poc: 0, field: "RefPicList0[0]" },
        { poc: 3, field: "RefPicList1[0]" } ] },
      { poc: 2, type: "B", decodeIdx: 4, refs: [
        { poc: 0, field: "RefPicList0[0]" },
        { poc: 99, field: "RefPicList1[0]" } ] }, // 悬空引用：POC 99 不存在
      { poc: 3, type: "P", decodeIdx: 1, refs: [
        { poc: 0, field: "RefPicList0[0]" },
        { poc: 5, field: "RefPicList0[1]" } ] }, // 与 POC 5 构成依赖环
      { poc: 4, type: "B", decodeIdx: 6, refs: [
        { poc: 3, field: "RefPicList0[0]" },
        { poc: 6, field: "RefPicList1[0]" } ] },
      { poc: 5, type: "P", decodeIdx: 5, refs: [
        { poc: 3, field: "RefPicList0[0]" } ] }, // 环：3 -> 5 -> 3
      { poc: 6, type: "P", decodeIdx: 2, refs: [
        { poc: 3, field: "RefPicList0[0]" } ] },
      { poc: 7, type: "B", decodeIdx: 7, refs: [] }, // 孤儿帧：B 帧却无任何参考
      { poc: 8, type: "B", decodeIdx: 8, refs: [
        { poc: 6, field: "RefPicList0[0]" },
        { poc: 6, field: "RefPicList1[0]" } ] },
    ],
  },
};
if (typeof module !== "undefined") module.exports = { DATASETS };
