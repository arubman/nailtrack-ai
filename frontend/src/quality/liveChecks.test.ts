import { describe, expect, it } from "vitest";
import { glareRatio, laplacianVariance, tiltDegrees } from "./liveChecks";

describe("live quality checks", () => {
  it("scores a flat pose at 0 degrees and a steep pose near 45", () => {
    expect(tiltDegrees({ x: 0, y: 0, z: 0 }, { x: 0, y: 1, z: 0 })).toBeCloseTo(0);
    expect(tiltDegrees({ x: 0, y: 0, z: 0 }, { x: 0, y: 0.1, z: 0.1 })).toBeCloseTo(45, 0);
  });

  it("gives a constant image no focus and a checkerboard a high score", () => {
    const flat = new Uint8ClampedArray(12 * 12).fill(40);
    expect(laplacianVariance(flat, 12, 12)).toBe(0);
    const checker = new Uint8ClampedArray(12 * 12);
    for (let i = 0; i < checker.length; i += 1) checker[i] = (i + Math.floor(i / 12)) % 2 === 0 ? 0 : 255;
    expect(laplacianVariance(checker, 12, 12)).toBeGreaterThan(1000);
  });

  it("counts only bright unsaturated pixels as glare", () => {
    const pixels = new Uint8ClampedArray([
      255, 255, 255, 255,
      200, 10, 10, 255,
    ]);
    expect(glareRatio(pixels, 2, 1)).toBeCloseTo(0.5);
  });
});
