import { describe, expect, it, vi } from "vitest";
import { NailTrackClient } from "./client";
import { isQualityRejection } from "./types";

function clientWith(fetchImpl: typeof fetch) {
  vi.stubGlobal("fetch", fetchImpl);
  return new NailTrackClient("http://api.test", { getAccessToken: async () => "token-1" });
}

describe("NailTrackClient", () => {
  it("sends the bearer token and parses the session", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ user: { id: "u", email: null, display_name: "A" }, auth_mode: "entra" }));
    const client = clientWith(fetchImpl as unknown as typeof fetch);
    const session = await client.session();
    expect(session.auth_mode).toBe("entra");
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer token-1");
  });

  it("omits Authorization when the token source returns null", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ items: [], total: 0 }));
    vi.stubGlobal("fetch", fetchImpl);
    const client = new NailTrackClient("http://api.test", { getAccessToken: async () => null });
    await client.treatments();
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(new Headers(init.headers).get("Authorization")).toBeNull();
  });

  it("throws the raw 422 quality body", async () => {
    const body = { passed: false, reasons: ["RETAKE_BLURRY"], messages: ["Hold steady"], metrics: { passed: false } };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status: 422 })));
    const client = new NailTrackClient("http://api.test", { getAccessToken: async () => null });
    await expect(client.analyze("L_INDEX", new Blob(["x"]), false)).rejects.toMatchObject({ status: 422, body });
    expect(isQualityRejection(body)).toBe(true);
  });
});

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
