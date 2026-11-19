function clamp(value, min, max) {
  return value < min ? min : value > max ? max : value;
}

export function parseRawArray(text) {
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new Error(`JSON 解析失败：${error.message}`);
  }

  if (Array.isArray(parsed) && parsed.every(Array.isArray)) {
    throw new Error("二维数组缺少排列声明，请使用包含 pattern、width、height、data 的 JSON 对象。");
  }
  if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.data)) {
    throw new Error("需要 JSON 对象，且 data 必须是数组。");
  }

  const pattern = String(parsed.pattern ?? "").toUpperCase();
  if (pattern !== "RGGB" && pattern !== "BGGR") {
    throw new Error("pattern 必须为 RGGB 或 BGGR。");
  }

  let width;
  let height;
  let values;
  if (parsed.data.length && Array.isArray(parsed.data[0])) {
    height = parsed.data.length;
    width = parsed.data[0].length;
    if (parsed.data.some((row) => !Array.isArray(row) || row.length !== width)) {
      throw new Error("二维 data 的各行长度必须一致。");
    }
    values = parsed.data.flat();
  } else {
    width = Number(parsed.width);
    height = Number(parsed.height);
    values = parsed.data;
  }

  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1) {
    throw new Error("width 和 height 必须是正整数。");
  }
  if (width > 2048 || height > 2048) {
    throw new Error("为保证交互流畅，单边尺寸不能超过 2048 像素。");
  }
  if (values.length !== width * height) {
    throw new Error(`data 长度应为 ${width * height}，当前为 ${values.length}。`);
  }

  const raw = new Float32Array(values.length);
  let maxValue = 0;
  for (let i = 0; i < values.length; i += 1) {
    const value = Number(values[i]);
    if (!Number.isFinite(value)) throw new Error(`data[${i}] 不是有效数字。`);
    if (value < 0) throw new Error(`data[${i}] 不能为负数。`);
    raw[i] = value;
    if (value > maxValue) maxValue = value;
  }

  let scale;
  if (Number.isFinite(Number(parsed.maxValue)) && Number(parsed.maxValue) > 0) {
    scale = Number(parsed.maxValue);
  } else if (Number.isFinite(Number(parsed.bitDepth)) && Number(parsed.bitDepth) > 0) {
    scale = 2 ** Number(parsed.bitDepth) - 1;
  } else if (maxValue <= 1.000001) {
    scale = 1;
  } else if (maxValue <= 255) {
    scale = 255;
  } else {
    scale = maxValue;
  }

  for (let i = 0; i < raw.length; i += 1) raw[i] = clamp(raw[i] / scale, 0, 1);
  return { pattern, width, height, raw, maxValue, scale };
}
