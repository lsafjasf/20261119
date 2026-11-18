export const WINDOW_TYPES = Object.freeze([
  "hann",
  "hamming",
  "triangle",
  "blackman",
  "rect"
]);

export const WINDOW_LABELS = Object.freeze({
  hann: "Hann",
  hamming: "Hamming",
  triangle: "Triangle",
  blackman: "Blackman",
  rect: "Rect"
});

const TAU = Math.PI * 2;

export function windowValue(type, phase) {
  const p = Math.min(1, Math.max(0, phase));
  switch (type) {
    case "hamming":
      return 0.54 - 0.46 * Math.cos(TAU * p);
    case "triangle":
      return 1 - Math.abs(2 * p - 1);
    case "blackman":
      return 0.42 - 0.5 * Math.cos(TAU * p) + 0.08 * Math.cos(2 * TAU * p);
    case "rect":
      return 1;
    case "hann":
    default:
      return 0.5 - 0.5 * Math.cos(TAU * p);
  }
}

export function normalizeWindowType(type) {
  return WINDOW_TYPES.includes(type) ? type : "hann";
}
