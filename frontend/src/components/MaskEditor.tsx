import { useEffect, useRef, useState, type PointerEvent } from "react";
import type { NailTrackClient } from "../api/client";
import type { CaptureMeasurement } from "../api/types";

const CLASSES = [
  { id: 1, label: "Lunula" },
  { id: 2, label: "Nail bed" },
  { id: 3, label: "Free margin" },
  { id: 0, label: "Background" },
] as const;

export function MaskEditor({
  client,
  capture,
  onSaved,
}: {
  client: NailTrackClient;
  capture: CaptureMeasurement | null;
  onSaved: () => void;
}) {
  const viewRef = useRef<HTMLCanvasElement>(null);
  const [klass, setKlass] = useState(2);
  const [status, setStatus] = useState("Save a measurement, then correct the mask if the classes are wrong.");
  const mask = useRef<Uint8Array | null>(null);
  const photoRef = useRef<HTMLImageElement | null>(null);
  const size = useRef({ width: 0, height: 0 });

  useEffect(() => {
    if (!capture?.aligned_image) return;
    let stop = false;
    void (async () => {
      const photo = await blobToImage(await client.openImage(capture.aligned_image!));
      if (stop) return;
      size.current = { width: photo.naturalWidth, height: photo.naturalHeight };
      const indices = new Uint8Array(photo.naturalWidth * photo.naturalHeight);
      const existing = capture.corrected_mask ?? capture.segmentation_mask;
      if (existing) {
        const painted = await blobToImage(await client.openImage(existing));
        const sample = document.createElement("canvas");
        sample.width = photo.naturalWidth;
        sample.height = photo.naturalHeight;
        const context = sample.getContext("2d");
        context?.drawImage(painted, 0, 0, sample.width, sample.height);
        const pixels = context?.getImageData(0, 0, sample.width, sample.height).data;
        if (pixels) {
          for (let i = 0; i < indices.length; i += 1) indices[i] = pixels[i * 4] <= 3 ? pixels[i * 4] : 0;
        }
      }
      mask.current = indices;
      photoRef.current = photo;
      paint(viewRef.current, photo, indices);
      setStatus("Paint lunula, nail bed, and free margin, then save the correction.");
    })().catch((error: unknown) => setStatus(error instanceof Error ? error.message : "Could not load the mask"));
    return () => {
      stop = true;
    };
  }, [capture, client]);

  function paintAt(event: PointerEvent<HTMLCanvasElement>) {
    const canvas = viewRef.current;
    const indices = mask.current;
    if (!canvas || !indices) return;
    const rect = canvas.getBoundingClientRect();
    const x = Math.floor(((event.clientX - rect.left) / rect.width) * size.current.width);
    const y = Math.floor(((event.clientY - rect.top) / rect.height) * size.current.height);
    const radius = Math.max(4, Math.round(size.current.width / 80));
    for (let dy = -radius; dy <= radius; dy += 1) {
      for (let dx = -radius; dx <= radius; dx += 1) {
        if (dx * dx + dy * dy > radius * radius) continue;
        const px = x + dx;
        const py = y + dy;
        if (px < 0 || py < 0 || px >= size.current.width || py >= size.current.height) continue;
        indices[py * size.current.width + px] = klass;
      }
    }
    const photo = photoRef.current;
    if (photo) paint(canvas, photo, indices);
  }

  async function save() {
    const indices = mask.current;
    if (!indices || !capture) return;
    const blob = await indicesToPng(indices, size.current.width, size.current.height);
    await client.submitMask(capture.id, blob);
    setStatus("Corrected mask saved. Forecasts were refreshed.");
    onSaved();
  }

  return (
    <section className="panel" aria-labelledby="mask-heading">
      <h2 id="mask-heading">Mask correction</h2>
      <p className="hint">{status}</p>
      <div className="segmented" role="group" aria-label="Mask class">
        {CLASSES.map((item) => (
          <button key={item.id} type="button" aria-pressed={klass === item.id} onClick={() => setKlass(item.id)}>{item.label}</button>
        ))}
      </div>
      <canvas
        ref={viewRef}
        className="stage"
        width={640}
        height={800}
        aria-label="Segmentation mask editor"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          paintAt(event);
        }}
        onPointerMove={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) paintAt(event);
        }}
      />
      <button type="button" className="primary" onClick={() => void save()} disabled={!capture}>Save mask</button>
    </section>
  );
}

function paint(canvas: HTMLCanvasElement | null, photo: HTMLImageElement, indices: Uint8Array) {
  if (!canvas) return;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.drawImage(photo, 0, 0, canvas.width, canvas.height);
  const overlay = context.getImageData(0, 0, canvas.width, canvas.height);
  const scaleX = photo.naturalWidth / canvas.width;
  const scaleY = photo.naturalHeight / canvas.height;
  for (let y = 0; y < canvas.height; y += 1) {
    for (let x = 0; x < canvas.width; x += 1) {
      const source = indices[Math.floor(y * scaleY) * photo.naturalWidth + Math.floor(x * scaleX)];
      if (!source) continue;
      const offset = (y * canvas.width + x) * 4;
      const tint = tintRgb(source);
      overlay.data[offset] = Math.round(overlay.data[offset] * 0.45 + tint[0] * 0.55);
      overlay.data[offset + 1] = Math.round(overlay.data[offset + 1] * 0.45 + tint[1] * 0.55);
      overlay.data[offset + 2] = Math.round(overlay.data[offset + 2] * 0.45 + tint[2] * 0.55);
    }
  }
  context.putImageData(overlay, 0, 0);
}

function tintRgb(klass: number): [number, number, number] {
  if (klass === 1) return [236, 220, 198];
  if (klass === 2) return [184, 86, 82];
  return [212, 158, 72];
}

async function blobToImage(blob: Blob) {
  const image = new Image();
  image.src = URL.createObjectURL(blob);
  await image.decode();
  return image;
}

function indicesToPng(indices: Uint8Array, width: number, height: number) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) return Promise.reject(new Error("canvas unavailable"));
  const image = context.createImageData(width, height);
  for (let i = 0; i < indices.length; i += 1) {
    const value = indices[i];
    image.data[i * 4] = value;
    image.data[i * 4 + 1] = value;
    image.data[i * 4 + 2] = value;
    image.data[i * 4 + 3] = 255;
  }
  context.putImageData(image, 0, 0);
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("PNG encoding failed"))), "image/png");
  });
}
