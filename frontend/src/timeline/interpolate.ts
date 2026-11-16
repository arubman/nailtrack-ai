import type { MeshVertex, TimelineFrame } from "../api/types";

export interface Bracket<T> {
  left: T;
  right: T;
  /** 0 at left, 1 at right. */
  weight: number;
}

/** Pick the frames on either side of a slider day. Exact hits have weight 0. */
export function bracketFrames<T extends { t_days: number }>(frames: T[], day: number): Bracket<T> {
  if (frames.length === 0) throw new Error("timeline has no frames");
  const sorted = [...frames].sort((a, b) => a.t_days - b.t_days);
  if (day <= sorted[0].t_days) return { left: sorted[0], right: sorted[0], weight: 0 };
  const last = sorted[sorted.length - 1];
  if (day >= last.t_days) return { left: last, right: last, weight: 0 };
  const rightIndex = sorted.findIndex((frame) => frame.t_days >= day);
  const right = sorted[rightIndex];
  if (right.t_days === day) return { left: right, right, weight: 0 };
  const left = sorted[rightIndex - 1];
  return { left, right, weight: (day - left.t_days) / (right.t_days - left.t_days) };
}

export function lerp(a: number, b: number, weight: number): number {
  return a + (b - a) * weight;
}

/** Interpolate control-mesh displacement. Positions stay on the left frame's grid. */
export function lerpMesh(left: MeshVertex[], right: MeshVertex[], weight: number): MeshVertex[] {
  const count = Math.min(left.length, right.length);
  const mixed: MeshVertex[] = [];
  for (let index = 0; index < count; index += 1) {
    const a = left[index];
    const b = right[index];
    mixed.push({
      x: a.x,
      y: a.y,
      dx: lerp(a.dx, b.dx, weight),
      dy: lerp(a.dy, b.dy, weight),
      pinned: a.pinned && b.pinned,
    });
  }
  return mixed;
}

/**
 * Server meshes already include active treatments (alpha_tx).
 * Turning the simulation off scales displacement back to alpha 1.
 */
export function treatmentDisplacementScale(alphaTx: number, simulateTreatment: boolean): number {
  if (simulateTreatment) return 1;
  if (!Number.isFinite(alphaTx) || alphaTx === 0) return 1;
  return 1 / alphaTx;
}

export function scaleMesh(mesh: MeshVertex[], scale: number): MeshVertex[] {
  return mesh.map((vertex) => ({ ...vertex, dx: vertex.dx * scale, dy: vertex.dy * scale }));
}

export function frameImageUrl(frame: TimelineFrame | undefined): string | null {
  return frame?.image?.url ?? null;
}
