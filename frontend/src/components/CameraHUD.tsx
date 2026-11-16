import { useEffect, useRef, useState } from "react";
import { ApiError, isQualityRejection, type FingerCode, type SessionRead } from "../api/types";
import type { NailTrackClient } from "../api/client";
import { fingerJoints, glareRatio, laplacianVariance, tiltDegrees, type Vec3 } from "../quality/liveChecks";

interface Props {
  client: NailTrackClient;
  finger: FingerCode;
  session: SessionRead;
  onSaved: () => void;
}

type Mode = "measure" | "calibrate";

interface Pill {
  label: string;
  ok: boolean;
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
  const [busy, setBusy] = useState(false);
  const landmarker = useRef<HandVideo | null>(null);

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
      landmarker.current = await loadHandLandmarker();
      const tick = () => {
        if (!stop) void sample(video, frame, ctx, finger, session, landmarker.current, setPills);
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
    setBusy(true);
    setReasons([]);
    const shot = document.createElement("canvas");
    shot.width = video.videoWidth;
    shot.height = video.videoHeight;
    shot.getContext("2d")?.drawImage(video, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) => shot.toBlob(resolve, "image/jpeg", 0.92));
    if (!blob) {
      setBusy(false);
      return;
    }
    try {
      if (mode === "calibrate") {
        await client.calibrate(finger, blob);
        setStatus("Calibration saved. The card scale and white patch are stored for this finger.");
      } else {
        await client.analyze(finger, blob, false);
        setStatus("Measurement saved.");
      }
      onSaved();
    } catch (error) {
      if (error instanceof ApiError && isQualityRejection(error.body)) {
        setReasons(error.body.messages.length ? error.body.messages : error.body.reasons);
        setStatus("The server asked for a retake.");
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
        <ul className="pills" aria-label="Live quality">
          {pills.map((pill) => (
            <li key={pill.label} className={pill.ok ? "ok" : "bad"}>
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
      <button type="button" className="primary" onClick={() => void capture()} disabled={busy}>
        {busy ? "Sending…" : mode === "calibrate" ? "Save calibration" : "Save measurement"}
      </button>
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
  detectForVideo: (video: HTMLVideoElement, timestamp: number) => {
    landmarks?: { x: number; y: number; z: number }[][];
    worldLandmarks?: { x: number; y: number; z: number }[][];
  };
}

async function loadHandLandmarker(): Promise<HandVideo | null> {
  try {
    const vision = await import("@mediapipe/tasks-vision");
    const files = await vision.FilesetResolver.forVisionTasks(
      "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21/wasm",
    );
    return vision.HandLandmarker.createFromOptions(files, {
      baseOptions: { modelAssetPath: "/models/hand_landmarker.task" },
      runningMode: "VIDEO",
      numHands: 1,
    });
  } catch {
    return null;
  }
}

async function sample(
  video: HTMLVideoElement,
  frame: HTMLCanvasElement,
  ctx: CanvasRenderingContext2D | null,
  finger: FingerCode,
  session: SessionRead,
  marker: HandVideo | null,
  setPills: (pills: Pill[]) => void,
) {
  if (!ctx || video.videoWidth === 0) return;
  const limits = session.quality_thresholds;
  let tilt: number | null = null;
  let box = { x: 0.28, y: 0.2, w: 0.44, h: 0.62 };
  if (marker) {
    const result = marker.detectForVideo(video, performance.now());
    const world = result.worldLandmarks?.[0];
    const image = result.landmarks?.[0];
    if (world && image) {
      const [tip, dip] = fingerJoints(finger);
      const tipPoint: Vec3 = world[tip] ?? { x: 0, y: 0, z: 0 };
      const dipPoint: Vec3 = world[dip] ?? tipPoint;
      tilt = tiltDegrees(dipPoint, tipPoint);
      box = boxAround(image[tip], image[dip]);
    }
  }
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
    { label: "Focus", ok: focus >= limits.blur_laplacian_min, detail: focus.toFixed(0) },
    { label: `Glare <${Math.round(limits.glare_ratio_max * 100)}%`, ok: glare < limits.glare_ratio_max, detail: `${Math.round(glare * 100)}%` },
    { label: `Flat pose <${limits.tilt_deg_max}°`, ok: tilt !== null && tilt < limits.tilt_deg_max, detail: tilt === null ? "no hand" : `${tilt.toFixed(0)}°` },
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
