import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./profile";
import { emptyOverview, getOverview, type Overview } from "./overview";
import { computeSteps } from "@/components/stepper/computeSteps";

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
  jobs: { open: 340, outside_filters: 12 },
  matches: { scored: 25, pending: 5 },
  feeds: { active: 2 },
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

  it("corpo sem seções vira os padrões seguros (computeSteps não quebra)", async () => {
    const partial = {
      profile: { confirmed: true, version: 2, vectorized: true },
      companies: { total: 3, active: 2 },
      jobs: { open: 9 },
    };
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(200, partial)));
    const o = await getOverview();
    expect(o.interview).toEqual({ state: "none" });
    expect(o.matches).toEqual({ scored: 0, pending: 0 });
    expect(o.feeds).toEqual({ active: 0 });
    expect(o.profile).toEqual({ confirmed: true, version: 2, vectorized: true, name: null, email: null });
    expect(o.companies).toEqual({ total: 3, active: 2, auto: 0 });
    expect(() => computeSteps(o)).not.toThrow();
  });

  it("corpo nulo ou com seção que não é objeto também cai nos padrões", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(200, { matches: null, jobs: 5 })));
    expect(await getOverview()).toEqual(emptyOverview());
  });

  it("nome/e-mail que não são texto viram null (a casca nunca quebra)", async () => {
    const bad = { profile: { confirmed: true, version: 1, vectorized: true, name: 42, email: { x: 1 } } };
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(200, bad)));
    const o = await getOverview();
    expect(o.profile.name).toBeNull();
    expect(o.profile.email).toBeNull();
    expect(o.profile.confirmed).toBe(true);
  });

  it("repassa o AbortSignal ao fetch", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, sample));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await getOverview(controller.signal);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.signal).toBe(controller.signal);
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
      jobs: { open: 0, outside_filters: 0 },
      matches: { scored: 0, pending: 0 },
      feeds: { active: 0 },
    });
  });
});
