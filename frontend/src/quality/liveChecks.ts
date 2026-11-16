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
