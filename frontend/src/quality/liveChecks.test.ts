import { describe, expect, it } from "vitest";
import {
  canForceSave,
  detectionSize,
  glareRatio,
  laplacianVariance,
  median,
  monotonicTimestamp,
  nextTiltWindow,
  poseCheck,
  tiltDegrees,
} from "./liveChecks";

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

  it("smooths tilt with a median of the last ten detections", () => {
    let history: number[] = [];
    const samples = [2, 4, 40, 3, 5, 6, 1, 8, 7, 9, 10];
    let smoothed: number | null = null;
    for (const sample of samples) {
      const next = nextTiltWindow(history, sample);
      history = next.history;
      smoothed = next.smoothed;
    }
    expect(history).toHaveLength(10);
    expect(history[0]).toBe(4);
    expect(median(history)).toBe(smoothed);
    expect(smoothed).toBe(6.5);
    const missed = nextTiltWindow(history, null);
    expect(missed.history).toEqual(history);
    expect(missed.smoothed).toBeNull();
  });

  it("treats a hidden hand as advisory and a steep pose as a fail", () => {
    expect(poseCheck({ modelFailed: true, handVisible: false, tilt: null, maxTilt: 10 })).toEqual({
      tone: "advisory",
      detail: "hand model failed to load",
    });
    expect(poseCheck({ modelFailed: false, handVisible: false, tilt: null, maxTilt: 10 })).toEqual({
      tone: "advisory",
      detail: "hand not visible",
    });
    expect(poseCheck({ modelFailed: false, handVisible: true, tilt: 4, maxTilt: 10 }).tone).toBe("ok");
    expect(poseCheck({ modelFailed: false, handVisible: true, tilt: 22, maxTilt: 10 }).tone).toBe("bad");
  });

  it("lets the user save a missing hand, and not a missing nail", () => {
    expect(canForceSave(["RETAKE_NO_HAND"])).toBe(true);
    expect(canForceSave(["RETAKE_TILT", "RETAKE_BLURRY"])).toBe(true);
    expect(canForceSave(["RETAKE_NO_HAND", "RETAKE_NO_NAIL"])).toBe(false);
    expect(canForceSave([])).toBe(false);
  });

  it("keeps detection timestamps increasing and shrinks a large frame", () => {
    expect(monotonicTimestamp(10, 12)).toBe(12);
    expect(monotonicTimestamp(10, 10)).toBe(11);
    expect(monotonicTimestamp(10, Number.NaN)).toBe(11);
    expect(detectionSize(1920, 1080)).toEqual({ width: 640, height: 360 });
    expect(detectionSize(400, 300)).toEqual({ width: 400, height: 300 });
  });

  it("counts only bright unsaturated pixels as glare", () => {
    const pixels = new Uint8ClampedArray([
      255, 255, 255, 255,
      200, 10, 10, 255,
    ]);
    expect(glareRatio(pixels, 2, 1)).toBeCloseTo(0.5);
  });
});
