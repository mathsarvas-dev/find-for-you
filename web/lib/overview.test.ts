import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./profile";
import { emptyOverview, getOverview, type Overview } from "./overview";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const sample: Overview = {
  profile: { confirmed: true, version: 3, vectorized: true, name: "Ana", email: "ana@example.com" },
  interview: { state: "in_progress" },
  companies: { total: 12, active: 10, auto: 4 },
  jobs: { open: 340 },
  matches: { scored: 25, pending: 5 },
};

describe("getOverview", () => {
  it("busca GET /overview e devolve o corpo no formato do contrato", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, sample));
    vi.stubGlobal("fetch", fetchMock);
    const o = await getOverview();
    expect(o).toEqual(sample);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/overview$/);
    expect(init.method ?? "GET").toBe("GET");
  });

  it("erro com `detail` vira ApiError com a mensagem do servidor", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(500, { detail: "Banco indisponível." })));
    const err = await getOverview().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
    expect(err.message).toBe("Banco indisponível.");
  });

  it("sem resposta vira ApiError com status null", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    const err = await getOverview().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBeNull();
  });
});

describe("emptyOverview", () => {
  it("espelha os padrões seguros da API", () => {
    expect(emptyOverview()).toEqual({
      profile: { confirmed: false, version: null, vectorized: false, name: null, email: null },
      interview: { state: "none" },
      companies: { total: 0, active: 0, auto: 0 },
      jobs: { open: 0 },
      matches: { scored: 0, pending: 0 },
    });
  });
});
