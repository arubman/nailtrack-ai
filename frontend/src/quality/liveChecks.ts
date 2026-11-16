/** In-browser quality numbers. Thresholds come from GET /api/v1/me. */

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

const TIP_AND_DIP: Record<string, [number, number]> = {
  THUMB: [4, 3],
  INDEX: [8, 7],
  MIDDLE: [12, 11],
  RING: [16, 15],
  PINKY: [20, 19],
};

export const FORCE_SAVE_REASONS = new Set([
  "RETAKE_BLURRY",
  "RETAKE_GLARE",
  "RETAKE_TILT",
  "RETAKE_NO_HAND",
]);

export type PoseTone = "ok" | "bad" | "advisory";

/** Longest side used for HandLandmarker. Full-resolution iPhone frames are slower and less stable. */
export function detectionSize(width: number, height: number, maxEdge = 640): { width: number; height: number } {
  if (width < 1 || height < 1) return { width: 1, height: 1 };
  const longEdge = Math.max(width, height);
  if (longEdge <= maxEdge) return { width: Math.round(width), height: Math.round(height) };
  const scale = maxEdge / longEdge;
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/** VIDEO mode rejects a timestamp that is not strictly later than the previous one. */
export function monotonicTimestamp(previous: number, now: number): number {
  if (!Number.isFinite(now) || now <= previous) return previous + 1;
  return now;
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[mid];
  return (sorted[mid - 1] + sorted[mid]) / 2;
}

/** Keep the last detected tilts. A missed frame does not erase them, but it is not a measurement. */
export function nextTiltWindow(history: number[], sample: number | null, limit = 10): { history: number[]; smoothed: number | null } {
  if (sample === null || !Number.isFinite(sample)) return { history, smoothed: null };
  const next = [...history, sample].slice(-limit);
  return { history: next, smoothed: median(next) };
}

export function poseCheck(input: {
  modelFailed: boolean;
  handVisible: boolean;
  tilt: number | null;
  maxTilt: number;
}): { tone: PoseTone; detail: string } {
  if (input.modelFailed) return { tone: "advisory", detail: "hand model failed to load" };
  if (!input.handVisible || input.tilt === null) return { tone: "advisory", detail: "hand not visible" };
  const detail = `${input.tilt.toFixed(0)}°`;
  return input.tilt < input.maxTilt ? { tone: "ok", detail } : { tone: "bad", detail };
}

export function canForceSave(reasons: string[]): boolean {
  return reasons.length > 0 && reasons.every((reason) => FORCE_SAVE_REASONS.has(reason));
}

export function fingerJoints(fingerCode: string): [number, number] {
  const name = fingerCode.split("_")[1] ?? "INDEX";
  return TIP_AND_DIP[name] ?? TIP_AND_DIP.INDEX;
}

export function tiltDegrees(dip: Vec3, tip: Vec3): number {
  const dx = tip.x - dip.x;
  const dy = tip.y - dip.y;
  const dz = tip.z - dip.z;
  const norm = Math.hypot(dx, dy, dz);
  if (norm < 1e-8) return 90;
  return (Math.asin(Math.min(1, Math.abs(dz) / norm)) * 180) / Math.PI;
}

export function laplacianVariance(gray: Uint8ClampedArray, width: number, height: number): number {
  if (width < 3 || height < 3) return 0;
  let count = 0;
  let sum = 0;
  let sumSq = 0;
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const center = gray[y * width + x];
      const value = 4 * center - gray[y * width + x - 1] - gray[y * width + x + 1] - gray[(y - 1) * width + x] - gray[(y + 1) * width + x];
      sum += value;
      sumSq += value * value;
      count += 1;
    }
  }
  if (count === 0) return 0;
  const mean = sum / count;
  return sumSq / count - mean * mean;
}

export function glareRatio(rgba: Uint8ClampedArray, width: number, height: number): number {
  let glare = 0;
  let total = width * height;
  if (total === 0) return 0;
  for (let index = 0; index < total; index += 1) {
    const offset = index * 4;
    const { s, v } = rgbToSv(rgba[offset], rgba[offset + 1], rgba[offset + 2]);
    if (v > 245 && s < 25) glare += 1;
  }
  return glare / total;
}

function rgbToSv(r: number, g: number, b: number): { s: number; v: number } {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const v = max;
  const s = max === 0 ? 0 : ((max - min) / max) * 255;
  return { s, v };
}
