import { useEffect, useRef, useState } from "react";
import { ApiError, isQualityRejection, type FingerCode, type SessionRead } from "../api/types";
import type { NailTrackClient } from "../api/client";
import {
  canForceSave,
  detectionSize,
  fingerJoints,
  glareRatio,
  laplacianVariance,
  monotonicTimestamp,
  nextTiltWindow,
  poseCheck,
  tiltDegrees,
  type Vec3,
} from "../quality/liveChecks";

interface Props {
  client: NailTrackClient;
  finger: FingerCode;
  session: SessionRead;
  onSaved: () => void;
}

type Mode = "measure" | "calibrate";

interface Pill {
  label: string;
  tone: "ok" | "bad" | "advisory";
  detail: string;
}

export function CameraHUD({ client, finger, session, onSaved }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [mode, setMode] = useState<Mode>("measure");
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const [pills, setPills] = useState<Pill[]>([]);
  const [status, setStatus] = useState("Starting the camera…");
  const [reasons, setReasons] = useState<string[]>([]);
  const [canOverride, setCanOverride] = useState(false);
  const [handHint, setHandHint] = useState("Keep the whole finger and the first knuckle in frame.");
  const [busy, setBusy] = useState(false);
  const landmarker = useRef<HandVideo | null>(null);
  const modelFailed = useRef(false);
  const tiltHistory = useRef<number[]>([]);
  const clock = useRef(0);
  const lastShot = useRef<Blob | null>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let stop = false;
    let stream: MediaStream | null = null;
    const frame = document.createElement("canvas");
    const ctx = frame.getContext("2d", { willReadFrequently: true });
    let timer = 0;

    void (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        });
      } catch {
        setStatus("Camera permission is required to capture a nail.");
        return;
      }
      if (stop) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      video.srcObject = stream;
      await video.play();
      setStatus(modeRef.current === "calibrate" ? "Hold the printed ArUco card beside the finger." : "Fill the stencil and hold still.");
      const loaded = await loadHandLandmarker();
      landmarker.current = loaded.landmarker;
      modelFailed.current = loaded.error !== null;
      if (loaded.error) setHandHint(loaded.error);
      const detectFrame = document.createElement("canvas");
      const detectCtx = detectFrame.getContext("2d", { willReadFrequently: true });
      const tick = () => {
        if (!stop) {
          sample(
            video,
            frame,
            ctx,
            detectFrame,
            detectCtx,
            finger,
            session,
            landmarker.current,
            modelFailed.current,
            tiltHistory,
            clock,
            setPills,
            setHandHint,
          );
        }
        timer = window.setTimeout(tick, 180);
      };
      tick();
    })();

    return () => {
      stop = true;
      window.clearTimeout(timer);
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, [finger, session]);

  useEffect(() => {
    setStatus(mode === "calibrate" ? "Hold the printed ArUco card beside the finger." : "Fill the stencil and hold still.");
  }, [mode]);

  async function capture() {
    const video = videoRef.current;
    if (!video || video.videoWidth === 0) return;
    const shot = document.createElement("canvas");
    shot.width = video.videoWidth;
    shot.height = video.videoHeight;
    shot.getContext("2d")?.drawImage(video, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) => shot.toBlob(resolve, "image/jpeg", 0.92));
    if (!blob) return;
    lastShot.current = blob;
    await upload(blob, false);
  }

  async function upload(blob: Blob, forceSave: boolean) {
    setBusy(true);
    setReasons([]);
    setCanOverride(false);
    try {
      if (mode === "calibrate") {
        await client.calibrate(finger, blob);
        setStatus("Calibration saved. The card scale and white patch are stored for this finger.");
      } else {
        await client.analyze(finger, blob, forceSave);
        setStatus("Measurement saved.");
      }
      onSaved();
    } catch (error) {
      if (error instanceof ApiError && isQualityRejection(error.body)) {
        setReasons(error.body.messages.length ? error.body.messages : error.body.reasons);
        setCanOverride(mode === "measure" && canForceSave(error.body.reasons));
        setStatus(canForceSave(error.body.reasons) ? "You can save this photo anyway." : "The server asked for a retake.");
      } else {
        setStatus(error instanceof Error ? error.message : "Upload failed");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="capture-heading">
      <div className="panel-head">
        <h2 id="capture-heading">Capture</h2>
        <div className="segmented" role="group" aria-label="Capture mode">
          <button type="button" aria-pressed={mode === "measure"} onClick={() => setMode("measure")}>Measure</button>
          <button type="button" aria-pressed={mode === "calibrate"} onClick={() => setMode("calibrate")}>Calibrate card</button>
        </div>
      </div>
      <p className="hint">
        {mode === "calibrate"
          ? "Place the printed ID-1 ArUco card (markers 0–3) in the frame with the nail. The server reads scale from the markers and white balance from the card patches."
          : "Use the rear camera. The stencil marks the cuticle, the side folds, and the free edge."}
      </p>
      <div className="viewport">
        <video ref={videoRef} playsInline muted autoPlay aria-label="Rear camera preview" />
        <FingerStencil />
        <p className="frame-hint">{handHint}</p>
        <ul className="pills" aria-label="Live quality">
          {pills.map((pill) => (
            <li key={pill.label} className={pill.tone}>
              <span>{pill.label}</span>
              <small>{pill.detail}</small>
            </li>
          ))}
        </ul>
      </div>
      {reasons.length > 0 && (
        <ul className="reasons" role="alert">
          {reasons.map((reason) => <li key={reason}>{reason}</li>)}
        </ul>
      )}
      <p className="status" aria-live="polite">{status}</p>
      <div className="row">
        <button type="button" className="primary" onClick={() => void capture()} disabled={busy}>
          {busy ? "Sending…" : mode === "calibrate" ? "Save calibration" : "Save measurement"}
        </button>
        {canOverride && (
          <button
            type="button"
            onClick={() => {
              const shot = lastShot.current;
              if (shot) void upload(shot, true);
            }}
            disabled={busy}
          >
            Save anyway
          </button>
        )}
      </div>
    </section>
  );
}

function FingerStencil() {
  return (
    <svg className="stencil" viewBox="0 0 100 160" aria-hidden="true">
      <path className="cuticle" d="M28 58 C36 48 64 48 72 58" />
      <path className="fold" d="M26 62 C24 90 24 118 30 138" />
      <path className="fold" d="M74 62 C76 90 76 118 70 138" />
      <path className="tip" d="M32 132 C40 146 60 146 68 132" />
      <text x="50" y="18" textAnchor="middle">cuticle</text>
      <text x="50" y="154" textAnchor="middle">free edge</text>
    </svg>
  );
}

interface HandVideo {
  detectForVideo: (video: HTMLVideoElement | HTMLCanvasElement, timestamp: number) => {
    landmarks?: { x: number; y: number; z: number }[][];
    worldLandmarks?: { x: number; y: number; z: number }[][];
  };
}

const WASM_URL = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21/wasm";

async function loadHandLandmarker(): Promise<{ landmarker: HandVideo | null; error: string | null }> {
  try {
    const vision = await import("@mediapipe/tasks-vision");
    const files = await vision.FilesetResolver.forVisionTasks(WASM_URL);
    try {
      return { landmarker: await createLandmarker(vision, files, "GPU"), error: null };
    } catch (gpuError) {
      console.error("Hand landmarker GPU delegate failed; trying CPU.", gpuError);
      return { landmarker: await createLandmarker(vision, files, "CPU"), error: null };
    }
  } catch (error) {
    console.error("Hand landmarker failed to load", error);
    return { landmarker: null, error: "hand model failed to load" };
  }
}

async function createLandmarker(
  vision: typeof import("@mediapipe/tasks-vision"),
  files: Awaited<ReturnType<typeof import("@mediapipe/tasks-vision").FilesetResolver.forVisionTasks>>,
  delegate: "GPU" | "CPU",
): Promise<HandVideo> {
  return vision.HandLandmarker.createFromOptions(files, {
    baseOptions: { modelAssetPath: "/models/hand_landmarker.task", delegate },
    runningMode: "VIDEO",
    numHands: 1,
    minHandDetectionConfidence: 0.3,
    minHandPresenceConfidence: 0.3,
    minTrackingConfidence: 0.3,
  });
}

function sample(
  video: HTMLVideoElement,
  frame: HTMLCanvasElement,
  ctx: CanvasRenderingContext2D | null,
  detectFrame: HTMLCanvasElement,
  detectCtx: CanvasRenderingContext2D | null,
  finger: FingerCode,
  session: SessionRead,
  marker: HandVideo | null,
  modelFailed: boolean,
  tiltHistory: { current: number[] },
  clock: { current: number },
  setPills: (pills: Pill[]) => void,
  setHandHint: (hint: string) => void,
) {
  if (!ctx || video.videoWidth === 0) return;
  const limits = session.quality_thresholds;
  let rawTilt: number | null = null;
  let box = { x: 0.28, y: 0.2, w: 0.44, h: 0.62 };
  if (marker && detectCtx) {
    const size = detectionSize(video.videoWidth, video.videoHeight);
    if (detectFrame.width !== size.width || detectFrame.height !== size.height) {
      detectFrame.width = size.width;
      detectFrame.height = size.height;
    }
    detectCtx.drawImage(video, 0, 0, size.width, size.height);
    clock.current = monotonicTimestamp(clock.current, performance.now());
    try {
      const result = marker.detectForVideo(detectFrame, clock.current);
      const world = result.worldLandmarks?.[0];
      const image = result.landmarks?.[0];
      if (world && image) {
        const [tip, dip] = fingerJoints(finger);
        const tipPoint: Vec3 = world[tip] ?? { x: 0, y: 0, z: 0 };
        const dipPoint: Vec3 = world[dip] ?? tipPoint;
        rawTilt = tiltDegrees(dipPoint, tipPoint);
        box = boxAround(image[tip], image[dip]);
      }
    } catch (error) {
      console.error("Hand detection failed", error);
    }
  }
  const windowed = nextTiltWindow(tiltHistory.current, rawTilt);
  tiltHistory.current = windowed.history;
  const pose = poseCheck({
    modelFailed,
    handVisible: rawTilt !== null,
    tilt: windowed.smoothed,
    maxTilt: limits.tilt_deg_max,
  });
  setHandHint(
    pose.detail === "hand not visible"
      ? "Show more of your finger. Keep the whole finger and the first knuckle in frame."
      : pose.detail === "hand model failed to load"
        ? "The hand model did not load. You can still take the photo; the server will check it."
        : "Keep the whole finger and the first knuckle in frame.",
  );
  const width = 96;
  const height = 128;
  frame.width = width;
  frame.height = height;
  ctx.drawImage(
    video,
    box.x * video.videoWidth,
    box.y * video.videoHeight,
    box.w * video.videoWidth,
    box.h * video.videoHeight,
    0,
    0,
    width,
    height,
  );
  const pixels = ctx.getImageData(0, 0, width, height).data;
  const gray = new Uint8ClampedArray(width * height);
  for (let i = 0; i < gray.length; i += 1) gray[i] = pixels[i * 4];
  const focus = laplacianVariance(gray, width, height);
  const glare = glareRatio(pixels, width, height);
  setPills([
    { label: "Focus", tone: focus >= limits.blur_laplacian_min ? "ok" : "bad", detail: focus.toFixed(0) },
    {
      label: `Glare <${Math.round(limits.glare_ratio_max * 100)}%`,
      tone: glare < limits.glare_ratio_max ? "ok" : "bad",
      detail: `${Math.round(glare * 100)}%`,
    },
    { label: `Flat pose <${limits.tilt_deg_max}°`, tone: pose.tone, detail: pose.detail },
  ]);
}

function boxAround(tip: { x: number; y: number }, dip: { x: number; y: number }) {
  const x0 = Math.min(tip.x, dip.x) - 0.12;
  const y0 = Math.min(tip.y, dip.y) - 0.12;
  const x1 = Math.max(tip.x, dip.x) + 0.12;
  const y1 = Math.max(tip.y, dip.y) + 0.18;
  return {
    x: clamp01(x0),
    y: clamp01(y0),
    w: Math.min(1, x1) - clamp01(x0),
    h: Math.min(1, y1) - clamp01(y0),
  };
}

function clamp01(value: number) {
  return Math.max(0, Math.min(1, value));
}
