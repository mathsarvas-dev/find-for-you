import { afterEach, describe, expect, it, vi } from "vitest";
import { getFunnel, outsideFiltersText, putFunnel } from "./funnel";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const state = {
  full: 3,
  stub: 2,
  gated: 1,
  floor: null,
  suggested_floor: 0.51,
  good_labels: 12,
};

describe("getFunnel", () => {
  it("lê GET /funnel", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(200, state)));
    expect(await getFunnel()).toEqual(state);
  });
});

describe("putFunnel", () => {
  it("envia o corte (ou null para desligar)", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { ...state, floor: 0.51 }));
    vi.stubGlobal("fetch", fetchMock);
    const res = await putFunnel(0.51);
    expect(res.floor).toBe(0.51);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/funnel$/);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ adherence_vec_floor: 0.51 });
  });

  it("null envia corte desligado", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, state));
    vi.stubGlobal("fetch", fetchMock);
    await putFunnel(null);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ adherence_vec_floor: null });
  });
});

describe("outsideFiltersText", () => {
  it("zero, um e plural com separador pt-BR", () => {
    expect(outsideFiltersText(0)).toBe("Nenhuma vaga fora dos seus filtros");
    expect(outsideFiltersText(1)).toBe("1 vaga fora dos seus filtros");
    expect(outsideFiltersText(1234)).toBe("1.234 vagas fora dos seus filtros");
  });
});
