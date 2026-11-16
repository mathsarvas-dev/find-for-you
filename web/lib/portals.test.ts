import { afterEach, describe, expect, it, vi } from "vitest";
import { listPortals, portalStatusLabel, setPortalEnabled } from "./portals";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listPortals", () => {
  it("lê GET /portals", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, []));
    vi.stubGlobal("fetch", fetchMock);
    expect(await listPortals()).toEqual([]);
    expect(String((fetchMock.mock.calls[0] as unknown as [string])[0])).toMatch(/\/portals$/);
  });
});

describe("setPortalEnabled", () => {
  it("envia PUT /portals/{id} com enabled", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { id: "vagas", enabled: true }));
    vi.stubGlobal("fetch", fetchMock);
    const res = await setPortalEnabled("vagas", true);
    expect(res.enabled).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/portals\/vagas$/);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ enabled: true });
  });
});

describe("portalStatusLabel", () => {
  it("traduz cada status para texto", () => {
    expect(portalStatusLabel("allowed")).toBe("Permitido");
    expect(portalStatusLabel("conditional")).toBe("Condicional");
    expect(portalStatusLabel("blocked")).toBe("Bloqueado");
  });
});
