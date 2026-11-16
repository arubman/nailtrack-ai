import { useEffect, useRef, useState } from "react";
import type { NailTrackClient } from "../api/client";
import type { ImageLink, MeshVertex, TimelineFeature, TimelineFrame, TimelineRead } from "../api/types";
import { bracketFrames, lerp, lerpMesh, scaleMesh, treatmentDisplacementScale } from "../timeline/interpolate";

const COLS = 16;
const ROWS = 20;

interface Props {
  client: NailTrackClient;
  timeline: TimelineRead | null;
}

export function TimelapseScrubber({ client, timeline }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [day, setDay] = useState(0);
  const [simulate, setSimulate] = useState(true);
  const [overlay, setOverlay] = useState(false);
  const dayRef = useRef(0);
  const targetRef = useRef(0);
  const simulateRef = useRef(true);
  const overlayRef = useRef(false);
  const timelineRef = useRef(timeline);
  timelineRef.current = timeline;
  simulateRef.current = simulate;
  overlayRef.current = overlay;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const gl = canvas.getContext("webgl", { antialias: true, premultipliedAlpha: false });
    if (!gl) return;
    const program = createProgram(gl);
    if (!program) return;
    const buffers = {
      position: gl.createBuffer(),
      left: createTexture(gl),
      right: createTexture(gl),
      mask: createTexture(gl),
    };
    let frame = 0;
    let alive = true;
    let drawing = false;
    const cache = new Map<string, HTMLImageElement>();

    const loop = () => {
      if (!alive) return;
      const current = timelineRef.current;
      dayRef.current = approach(dayRef.current, targetRef.current);
      if (current && current.frames.length > 0 && !drawing) {
        drawing = true;
        void draw(gl, program, buffers, current, dayRef.current, simulateRef.current, overlayRef.current, client, cache).finally(() => {
          drawing = false;
        });
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => {
      alive = false;
      cancelAnimationFrame(frame);
    };
  }, [client]);

  const bracket = timeline && timeline.frames.length > 0 ? bracketFrames(timeline.frames, day) : null;
  const lengths = bracket
    ? {
        bed: mixLength(bracket.left.nail_bed_length_mm, bracket.right.nail_bed_length_mm, bracket.weight),
        free: mixLength(bracket.left.free_margin_length_mm, bracket.right.free_margin_length_mm, bracket.weight),
      }
    : { bed: null, free: null };
  const features = (bracket ? (bracket.weight < 0.5 ? bracket.left : bracket.right).features : []) ?? [];

  return (
    <section className="panel" aria-labelledby="timeline-heading">
      <div className="panel-head">
        <h2 id="timeline-heading">Timeline</h2>
        <p className="day" aria-live="polite">{formatDay(day)}</p>
      </div>
      <canvas ref={canvasRef} className="stage" width={640} height={800} role="img" aria-label="Nail growth timelapse" />
      <label className="slider">
        <span className="sr-only">Day relative to the latest capture</span>
        <input
          type="range"
          min={-90}
          max={90}
          step={1}
          value={day}
          aria-valuemin={-90}
          aria-valuemax={90}
          aria-valuenow={day}
          aria-valuetext={formatDay(day)}
          onChange={(event) => {
            const next = Number(event.target.value);
            setDay(next);
            targetRef.current = next;
          }}
        />
      </label>
      <div className="callouts">
        <p><span>L_bed</span><strong>{lengths.bed === null ? "—" : `${lengths.bed.toFixed(2)} mm`}</strong></p>
        <p><span>L_free</span><strong>{lengths.free === null ? "—" : `${lengths.free.toFixed(2)} mm`}</strong></p>
        {features.map((feature) => (
          <p key={feature.id}>
            <span>{feature.type === "leukonychia" ? "Spot" : "Crack"}</span>
            <strong>{outgrowthLabel(feature, day)}</strong>
          </p>
        ))}
      </div>
      <label className="check">
        <input type="checkbox" checked={simulate} onChange={(event) => setSimulate(event.target.checked)} />
        Simulate Active Treatment Regimen
      </label>
      <label className="check">
        <input type="checkbox" checked={overlay} onChange={(event) => setOverlay(event.target.checked)} />
        Overlay Anatomical Segmentation Masks
      </label>
    </section>
  );
}

function formatDay(day: number) {
  if (day === 0) return "Today";
  return day > 0 ? `+${day} days` : `${day} days`;
}

function mixLength(left: number | null, right: number | null, weight: number) {
  if (left === null || right === null) return weight < 0.5 ? left : right;
  return lerp(left, right, weight);
}

function outgrowthLabel(feature: TimelineFeature, day: number) {
  if (feature.clipped) return "100% out";
  if (feature.days_until_clear == null) return "tracked";
  if (day <= 0) return "0% out";
  const percent = Math.min(100, (day / feature.days_until_clear) * 100);
  return `${percent.toFixed(0)}% out`;
}

function approach(current: number, target: number) {
  const delta = target - current;
  if (Math.abs(delta) < 0.05) return target;
  return current + delta * 0.18;
}

interface Program {
  program: WebGLProgram;
  src: number;
  dst: number;
  size: WebGLUniformLocation | null;
  weight: WebGLUniformLocation | null;
  overlay: WebGLUniformLocation | null;
}

interface Buffers {
  position: WebGLBuffer | null;
  left: WebGLTexture | null;
  right: WebGLTexture | null;
  mask: WebGLTexture | null;
}

const imageCache = new WeakMap<NailTrackClient, Map<string, Promise<HTMLImageElement>>>();

async function loadLinked(client: NailTrackClient, link: ImageLink | null): Promise<HTMLImageElement | null> {
  if (!link) return null;
  let bucket = imageCache.get(client);
  if (!bucket) {
    bucket = new Map();
    imageCache.set(client, bucket);
  }
  let pending = bucket.get(link.url);
  if (!pending) {
    pending = (async () => {
      const blob = await client.openImage(link);
      const image = new Image();
      image.src = URL.createObjectURL(blob);
      await image.decode();
      return image;
    })();
    bucket.set(link.url, pending);
  }
  return pending;
}

async function draw(
  gl: WebGLRenderingContext,
  program: Program,
  buffers: Buffers,
  timeline: TimelineRead,
  day: number,
  simulate: boolean,
  overlay: boolean,
  client: NailTrackClient,
  ready: Map<string, HTMLImageElement>,
) {
  const bracket = bracketFrames(timeline.frames, day);
  const forecasting = bracket.left.kind === "forecast" || bracket.right.kind === "forecast";
  const today = timeline.frames.find((frame) => frame.kind === "today") ?? bracket.left;
  const leftLink = forecasting ? today.image : bracket.left.image;
  const rightLink = forecasting ? today.image : bracket.right.image;
  const maskLink = (bracket.weight < 0.5 ? bracket.left : bracket.right).mask ?? today.mask;
  const [leftImage, rightImage, maskImage] = await Promise.all([
    remember(ready, leftLink, client),
    remember(ready, rightLink, client),
    overlay ? remember(ready, maskLink, client) : Promise.resolve(null),
  ]);
  if (!leftImage) return;
  const source = leftImage;
  const mesh = meshFor(bracket.left, bracket.right, bracket.weight, timeline.alpha_tx, simulate);
  gl.viewport(0, 0, gl.canvas.width, gl.canvas.height);
  gl.clearColor(0.12, 0.08, 0.07, 1);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.useProgram(program.program);
  upload(gl, buffers.left, source);
  upload(gl, buffers.right, rightImage ?? source);
  if (maskImage) upload(gl, buffers.mask, maskImage);
  gl.uniform2f(program.size, source.naturalWidth, source.naturalHeight);
  gl.uniform1f(program.weight, forecasting ? 0 : bracket.weight);
  gl.uniform1f(program.overlay, overlay && maskImage ? 1 : 0);
  bindSampler(gl, program.program, "u_left", buffers.left, 0);
  bindSampler(gl, program.program, "u_right", buffers.right, 1);
  bindSampler(gl, program.program, "u_mask", buffers.mask, 2);
  const data = mesh ? meshTriangles(mesh) : quadTriangles(source.naturalWidth, source.naturalHeight);
  gl.bindBuffer(gl.ARRAY_BUFFER, buffers.position);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
  gl.enableVertexAttribArray(program.src);
  gl.enableVertexAttribArray(program.dst);
  gl.vertexAttribPointer(program.src, 2, gl.FLOAT, false, 16, 0);
  gl.vertexAttribPointer(program.dst, 2, gl.FLOAT, false, 16, 8);
  gl.drawArrays(gl.TRIANGLES, 0, data.length / 4);
}

async function remember(cache: Map<string, HTMLImageElement>, link: ImageLink | null, client: NailTrackClient) {
  if (!link) return null;
  const cached = cache.get(link.url);
  if (cached) return cached;
  const image = await loadLinked(client, link);
  if (image) cache.set(link.url, image);
  return image;
}

function meshFor(left: TimelineFrame, right: TimelineFrame, weight: number, alpha: number, simulate: boolean): MeshVertex[] | null {
  const forecast = left.kind === "forecast" || right.kind === "forecast";
  if (!forecast) return null;
  const rightMesh = right.mesh;
  const leftMesh = left.mesh ?? (rightMesh ? rightMesh.map((vertex) => ({ ...vertex, dx: 0, dy: 0, pinned: true })) : null);
  if (!leftMesh || !rightMesh) return rightMesh ?? leftMesh;
  const scale = treatmentDisplacementScale(alpha, simulate);
  return scaleMesh(lerpMesh(leftMesh, rightMesh, weight), scale);
}

function meshTriangles(mesh: MeshVertex[]): Float32Array {
  if (mesh.length !== COLS * ROWS) return quadFromMesh(mesh);
  const data: number[] = [];
  const at = (row: number, col: number) => mesh[row * COLS + col];
  for (let row = 0; row < ROWS - 1; row += 1) {
    for (let col = 0; col < COLS - 1; col += 1) {
      pushTri(data, at(row, col), at(row, col + 1), at(row + 1, col));
      pushTri(data, at(row, col + 1), at(row + 1, col + 1), at(row + 1, col));
    }
  }
  return new Float32Array(data);
}

function quadFromMesh(mesh: MeshVertex[]) {
  const xs = mesh.map((vertex) => vertex.x);
  const ys = mesh.map((vertex) => vertex.y);
  return quadTriangles(Math.max(...xs), Math.max(...ys));
}

function quadTriangles(width: number, height: number) {
  const corner = (x: number, y: number): MeshVertex => ({ x, y, dx: 0, dy: 0, pinned: true });
  const data: number[] = [];
  pushTri(data, corner(0, 0), corner(width, 0), corner(0, height));
  pushTri(data, corner(width, 0), corner(width, height), corner(0, height));
  return new Float32Array(data);
}

function pushTri(data: number[], a: MeshVertex, b: MeshVertex, c: MeshVertex) {
  for (const vertex of [a, b, c]) {
    data.push(vertex.x, vertex.y, vertex.x + vertex.dx, vertex.y + vertex.dy);
  }
}

function createTexture(gl: WebGLRenderingContext) {
  const texture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  return texture;
}

function upload(gl: WebGLRenderingContext, texture: WebGLTexture | null, image: HTMLImageElement) {
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
}

function bindSampler(gl: WebGLRenderingContext, program: WebGLProgram, name: string, texture: WebGLTexture | null, unit: number) {
  gl.activeTexture(gl.TEXTURE0 + unit);
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.uniform1i(gl.getUniformLocation(program, name), unit);
}

function createProgram(gl: WebGLRenderingContext): Program | null {
  const vertex = compile(gl, gl.VERTEX_SHADER, `
    attribute vec2 a_src;
    attribute vec2 a_dst;
    uniform vec2 u_size;
    varying vec2 v_uv;
    void main() {
      v_uv = a_src / u_size;
      vec2 clip = (a_dst / u_size) * 2.0 - 1.0;
      gl_Position = vec4(clip.x, -clip.y, 0.0, 1.0);
    }
  `);
  const fragment = compile(gl, gl.FRAGMENT_SHADER, `
    precision mediump float;
    varying vec2 v_uv;
    uniform sampler2D u_left;
    uniform sampler2D u_right;
    uniform sampler2D u_mask;
    uniform float u_weight;
    uniform float u_overlay;
    void main() {
      vec4 color = mix(texture2D(u_left, v_uv), texture2D(u_right, v_uv), u_weight);
      if (u_overlay > 0.5) {
        float klass = texture2D(u_mask, v_uv).r * 255.0;
        vec3 tint = klass > 2.5 ? vec3(0.83, 0.62, 0.28) : klass > 1.5 ? vec3(0.72, 0.34, 0.32) : klass > 0.5 ? vec3(0.93, 0.86, 0.78) : vec3(0.0);
        if (klass > 0.5) color.rgb = mix(color.rgb, tint, 0.45);
      }
      gl_FragColor = color;
    }
  `);
  if (!vertex || !fragment) return null;
  const program = gl.createProgram();
  if (!program) return null;
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  return {
    program,
    src: gl.getAttribLocation(program, "a_src"),
    dst: gl.getAttribLocation(program, "a_dst"),
    size: gl.getUniformLocation(program, "u_size"),
    weight: gl.getUniformLocation(program, "u_weight"),
    overlay: gl.getUniformLocation(program, "u_overlay"),
  };
}

function compile(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return null;
  return shader;
}
