import { useCallback, useEffect, useMemo, useState } from "react";
import { NailTrackClient } from "./api/client";
import { FINGERS, type CaptureMeasurement, type FingerCode, type FingerSummary, type SessionRead, type TimelineRead } from "./api/types";
import { useAuth } from "./auth/AuthProvider";
import { CameraHUD } from "./components/CameraHUD";
import { MaskEditor } from "./components/MaskEditor";
import { NailMetricsCard } from "./components/NailMetricsCard";
import { TimelapseScrubber } from "./components/TimelapseScrubber";
import { TreatmentLogger } from "./components/TreatmentLogger";

type Tab = "capture" | "timeline" | "treatments";

export function App() {
  const auth = useAuth();
  const client = useMemo(() => new NailTrackClient(import.meta.env.VITE_API_BASE_URL ?? "", {
    getAccessToken: () => auth.getAccessToken(),
  }), [auth]);
  const [session, setSession] = useState<SessionRead | null>(null);
  const [fingers, setFingers] = useState<FingerSummary[]>([]);
  const [finger, setFinger] = useState<FingerCode>("L_INDEX");
  const [tab, setTab] = useState<Tab>("capture");
  const [timeline, setTimeline] = useState<TimelineRead | null>(null);
  const [capture, setCapture] = useState<CaptureMeasurement | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [nextSession, nextFingers] = await Promise.all([client.session(), client.fingers()]);
    setSession(nextSession);
    setFingers(nextFingers);
    try {
      setTimeline(await client.timeline(finger));
    } catch {
      setTimeline(null);
    }
  }, [client, finger]);

  useEffect(() => {
    if (!auth.ready || (!auth.devMode && !auth.account)) return;
    void refresh().catch((err: unknown) => setError(err instanceof Error ? err.message : "API unavailable"));
  }, [auth.account, auth.devMode, auth.ready, refresh]);

  if (!auth.ready) return <p className="boot">Starting NailTrack…</p>;
  if (!auth.devMode && !auth.account) {
    return (
      <main className="gate">
        <h1>NailTrack</h1>
        <p>Sign in with your Entra External ID account.</p>
        {auth.error && <p role="alert">{auth.error}</p>}
        <button type="button" className="primary" onClick={() => void auth.signIn()}>Sign in</button>
      </main>
    );
  }

  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <header className="top">
        <div>
          <p className="mark">NailTrack</p>
          <h1>Fingernail growth</h1>
        </div>
        {!auth.devMode && <button type="button" onClick={() => void auth.signOut()}>Sign out</button>}
      </header>
      <div className="fingers" role="radiogroup" aria-label="Finger">
        {FINGERS.map((code) => {
          const summary = fingers.find((item) => item.finger_code === code);
          return (
            <label key={code} className={code === finger ? "selected" : ""}>
              <input type="radio" name="finger" value={code} checked={code === finger} onChange={() => setFinger(code)} />
              <span>{code.replace("_", " ")}</span>
              <small>{summary?.baseline_id ? "calibrated" : "new"}</small>
            </label>
          );
        })}
      </div>
      <nav className="tabs" aria-label="Sections">
        {(["capture", "timeline", "treatments"] as const).map((item) => (
          <button key={item} type="button" aria-current={tab === item ? "page" : undefined} onClick={() => setTab(item)}>
            {item}
          </button>
        ))}
      </nav>
      <main id="main">
        {error && <p role="alert">{error}</p>}
        {tab === "capture" && session && (
          <>
            <CameraHUD
              client={client}
              finger={finger}
              session={session}
              onSaved={() => {
                void refresh();
                void client.timeline(finger).then(async (next) => {
                  setTimeline(next);
                  const today = next.frames.find((frame) => frame.kind === "today" && frame.capture_id);
                  if (!today?.capture_id) return;
                  const page = await client.fingers();
                  setFingers(page);
                  setCapture({
                    id: today.capture_id,
                    finger_code: finger,
                    nail_bed_length_mm: today.nail_bed_length_mm,
                    free_margin_length_mm: today.free_margin_length_mm,
                    total_length_mm: today.total_length_mm,
                    mask_source: null,
                    segmentation_mask: today.mask,
                    corrected_mask: null,
                    aligned_image: today.image,
                  });
                });
              }}
            />
            <MaskEditor client={client} capture={capture} onSaved={() => void refresh()} />
          </>
        )}
        {tab === "timeline" && (
          <>
            <TimelapseScrubber client={client} timeline={timeline} />
            <NailMetricsCard timeline={timeline} />
          </>
        )}
        {tab === "treatments" && (
          <TreatmentLogger client={client} finger={finger} onChanged={() => void refresh()} />
        )}
      </main>
    </>
  );
}
