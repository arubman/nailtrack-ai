import { describe, expect, it } from "vitest";
import type { MeshVertex } from "../api/types";
import { bracketFrames, lerpMesh, treatmentDisplacementScale } from "./interpolate";

const frames = [{ t_days: -10 }, { t_days: 0 }, { t_days: 7 }, { t_days: 14 }];

describe("bracketFrames", () => {
  it("clamps outside the timeline", () => {
    expect(bracketFrames(frames, -90).left.t_days).toBe(-10);
    expect(bracketFrames(frames, 90).right.t_days).toBe(14);
  });

  it("selects the surrounding frames and the blend weight", () => {
    const bracket = bracketFrames(frames, 7);
    expect(bracket.left.t_days).toBe(7);
    expect(bracket.weight).toBe(0);
    const between = bracketFrames(frames, 3.5);
    expect(between.left.t_days).toBe(0);
    expect(between.right.t_days).toBe(7);
    expect(between.weight).toBeCloseTo(0.5);
  });

  it("slider days from -90 to 90 step by 1", () => {
    const days: number[] = [];
    for (let day = -90; day <= 90; day += 1) days.push(day);
    expect(days).toHaveLength(181);
    expect(days[0]).toBe(-90);
    expect(days.at(-1)).toBe(90);
    expect(bracketFrames(frames, days[90]).left.t_days).toBe(0);
  });
});

describe("mesh interpolation and treatment scale", () => {
  const left: MeshVertex[] = [{ x: 1, y: 2, dx: 0, dy: 0, pinned: true }];
  const right: MeshVertex[] = [{ x: 1, y: 2, dx: 10, dy: 4, pinned: false }];

  it("lerps displacement", () => {
    const mixed = lerpMesh(left, right, 0.25);
    expect(mixed[0].dx).toBeCloseTo(2.5);
    expect(mixed[0].dy).toBeCloseTo(1);
    expect(mixed[0].pinned).toBe(false);
  });

  it("scales treatment displacement back to alpha 1 when the simulation is off", () => {
    expect(treatmentDisplacementScale(2, true)).toBe(1);
    expect(treatmentDisplacementScale(2, false)).toBe(0.5);
  });
});
