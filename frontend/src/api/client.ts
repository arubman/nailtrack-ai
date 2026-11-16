import {
  ApiError,
  type CaptureMeasurement,
  type FingerCode,
  type FingerSummary,
  type SessionRead,
  type TimelineRead,
  type Treatment,
  type TreatmentDraft,
} from "./types";

export interface TokenSource {
  getAccessToken(): Promise<string | null>;
}

export class NailTrackClient {
  constructor(
    private readonly baseUrl: string,
    private readonly auth: TokenSource,
  ) {}

  session(): Promise<SessionRead> {
    return this.json("/api/v1/me");
  }

  fingers(): Promise<FingerSummary[]> {
    return this.json("/api/v1/fingers");
  }

  timeline(finger: FingerCode): Promise<TimelineRead> {
    return this.json(`/api/v1/fingers/${finger}/timeline`);
  }

  treatments(): Promise<{ items: Treatment[]; total: number }> {
    return this.json("/api/v1/treatments?limit=100");
  }

  createTreatment(draft: TreatmentDraft): Promise<Treatment> {
    return this.json("/api/v1/treatments", { method: "POST", json: draft });
  }

  updateTreatment(id: string, patch: Partial<TreatmentDraft>): Promise<Treatment> {
    return this.json(`/api/v1/treatments/${id}`, { method: "PATCH", json: patch });
  }

  deleteTreatment(id: string): Promise<void> {
    return this.json(`/api/v1/treatments/${id}`, { method: "DELETE" });
  }

  calibrate(finger: FingerCode, image: Blob): Promise<unknown> {
    const body = new FormData();
    body.set("finger_code", finger);
    body.set("reference_type", "ID1_CARD");
    body.set("image", image, "capture.jpg");
    return this.send("/api/v1/calibrate", { method: "POST", body });
  }

  analyze(finger: FingerCode, image: Blob, forceSave: boolean): Promise<CaptureMeasurement> {
    const body = new FormData();
    body.set("finger_code", finger);
    body.set("force_save", forceSave ? "true" : "false");
    body.set("image", image, "capture.jpg");
    return this.send("/api/v1/captures/analyze", { method: "POST", body }) as Promise<CaptureMeasurement>;
  }

  submitMask(captureId: string, mask: Blob): Promise<CaptureMeasurement> {
    const body = new FormData();
    body.set("mask", mask, "mask.png");
    return this.send(`/api/v1/captures/${captureId}/mask`, { method: "POST", body }) as Promise<CaptureMeasurement>;
  }

  async openImage(link: { url: string; access: "sas" | "authenticated_route" }): Promise<Blob> {
    if (link.access === "sas") {
      const response = await fetch(link.url);
      if (!response.ok) throw new ApiError(response.status, await readBody(response));
      return response.blob();
    }
    return this.fetchBlob(link.url);
  }

  async fetchBlob(linkUrl: string): Promise<Blob> {
    const url = linkUrl.startsWith("http") ? linkUrl : `${this.baseUrl}${linkUrl}`;
    const headers = await this.headers(false);
    const response = await fetch(url, { headers });
    if (!response.ok) throw new ApiError(response.status, await readBody(response));
    return response.blob();
  }

  private json<T>(path: string, init: { method?: string; json?: unknown } = {}): Promise<T> {
    const headers = init.json ? { "Content-Type": "application/json" } : undefined;
    return this.send(path, {
      method: init.method ?? "GET",
      headers,
      body: init.json ? JSON.stringify(init.json) : undefined,
    }) as Promise<T>;
  }

  private async send(path: string, init: RequestInit): Promise<unknown> {
    const headers = await this.headers(true, init.headers);
    const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });
    if (response.status === 204) return undefined;
    const payload = await readBody(response);
    if (!response.ok) throw new ApiError(response.status, payload);
    return payload;
  }

  private async headers(json: boolean, extra?: HeadersInit): Promise<Headers> {
    const headers = new Headers(extra);
    if (!json) headers.delete("Content-Type");
    const token = await this.auth.getAccessToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return headers;
  }
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}
