import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./profile";
import {
  MAX_TARGETS,
  candidateStateLabel,
  discoveryCounters,
  getDiscovery,
  listCandidates,
  originKindLabel,
  parseTargets,
  putDiscovery,
  type DiscoveryState,
} from "./discovery";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const state: DiscoveryState = {
  enabled: true,
  target_companies: ["Nubank"],
  auto_companies: 3,
  max_auto: 30,
  pending: 12,
  found: 4,
};

describe("getDiscovery", () => {
  it("GET /discovery devolve preferências e contadores", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, state));
    await expect(getDiscovery()).resolves.toEqual(state);
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/discovery");
  });
});

describe("putDiscovery", () => {
  it("PUT /discovery com JSON", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, { ...state, enabled: false }));
    await expect(putDiscovery({ enabled: false, target_companies: ["Nubank"] })).resolves.toEqual({
      ...state,
      enabled: false,
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/discovery");
    expect(init?.method).toBe("PUT");
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init?.body))).toEqual({ enabled: false, target_companies: ["Nubank"] });
  });

  it("erro com detail em texto vira ApiError com a mensagem do servidor", async () => {
    mockFetch(async () => jsonResponse(503, { detail: "Banco indisponível." }));
    await expect(putDiscovery({ enabled: true, target_companies: [] })).rejects.toEqual(
      new ApiError(503, "Banco indisponível."),
    );
  });

  it("422 sem detail em texto ganha mensagem específica", async () => {
    mockFetch(async () => jsonResponse(422, { detail: [{ msg: "x" }] }));
    const err = await putDiscovery({ enabled: true, target_companies: ["a"] }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.message).toMatch(/até 50 nomes/);
  });
});

describe("listCandidates", () => {
  it("monta a query string com estado, limite e deslocamento", async () => {
    const page = { total: 0, items: [] };
    const fetchMock = mockFetch(async () => jsonResponse(200, page));
    await expect(listCandidates({ state: "pending", limit: 50, offset: 100 })).resolves.toEqual(page);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://localhost:8000/discovery/candidates?limit=50&offset=100&state=pending",
    );
  });

  it("sem estado não manda o parâmetro", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, { total: 0, items: [] }));
    await listCandidates({ limit: 50, offset: 0 });
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://localhost:8000/discovery/candidates?limit=50&offset=0",
    );
  });

  it("erro do servidor com detail", async () => {
    mockFetch(async () => jsonResponse(500, { detail: "Falhou." }));
    await expect(listCandidates({ limit: 50, offset: 0 })).rejects.toEqual(new ApiError(500, "Falhou."));
  });
});

describe("parseTargets", () => {
  it("uma por linha, sem linhas vazias e sem espaços nas pontas", () => {
    expect(parseTargets("  Nubank \n\n  \nGitLab\r\nStone")).toEqual({
      ok: true,
      names: ["Nubank", "GitLab", "Stone"],
    });
  });

  it("remove repetidos sem diferenciar maiúsculas e em NFC", () => {
    const decomposed = "Café Ltda";
    const composed = "Café Ltda";
    expect(parseTargets(`Nubank\nNUBANK\nnubank\n${decomposed}\n${composed}`)).toEqual({
      ok: true,
      names: ["Nubank", composed],
    });
  });

  it("até 50 nomes passa; 51 dá mensagem clara", () => {
    const fifty = Array.from({ length: MAX_TARGETS }, (_, i) => `Empresa ${i}`).join("\n");
    expect(parseTargets(fifty)).toMatchObject({ ok: true });
    const r = parseTargets(`${fifty}\nEmpresa extra`);
    expect(r).toEqual({ ok: false, error: "São 51 empresas; o limite é 50. Remova 1." });
  });

  it("repetidos não contam para o limite de 50", () => {
    const fifty = Array.from({ length: MAX_TARGETS }, (_, i) => `Empresa ${i}`).join("\n");
    expect(parseTargets(`${fifty}\nempresa 0\nEMPRESA 1`)).toMatchObject({ ok: true });
  });

  it("nome com mais de 80 caracteres dá mensagem com o começo do nome", () => {
    const long = "A".repeat(81);
    const r = parseTargets(`Nubank\n${long}`);
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error).toMatch(/tem 81 caracteres; o limite é 80/);
      expect(r.error).toContain("AAAAAAAAAA");
    }
    expect(parseTargets("B".repeat(80))).toMatchObject({ ok: true });
  });

  it("conta caracteres fora do BMP como um só", () => {
    expect(parseTargets("😀".repeat(80))).toMatchObject({ ok: true });
  });

  it("caractere de controle ou invisível é recusado", () => {
    expect(parseTargets("Nu\tbank")).toMatchObject({ ok: false });
    expect(parseTargets("Nu​bank")).toMatchObject({ ok: false });
  });

  it("lista vazia é válida", () => {
    expect(parseTargets("  \n ")).toEqual({ ok: true, names: [] });
  });
});

describe("apresentação", () => {
  it("contadores: 'na fila', nunca 'em validação'", () => {
    expect(discoveryCounters({ found: 12, pending: 34 })).toBe("12 empresas encontradas · 34 na fila");
    expect(discoveryCounters({ found: 1, pending: 0 })).toBe("1 empresa encontrada · 0 na fila");
  });

  it("rótulos de estado do candidato", () => {
    expect(candidateStateLabel("pending")).toBe("na fila");
    expect(candidateStateLabel("valid")).toBe("adicionada");
    expect(candidateStateLabel("invalid")).toBe("sem vagas ou inexistente");
    expect(candidateStateLabel("known")).toBe("já existe");
    expect(candidateStateLabel("rejected")).toBe("ignorada");
    expect(candidateStateLabel("<b>novo</b>")).toBe("—");
  });

  it("rótulos de origem", () => {
    expect(originKindLabel("catalog")).toBe("catálogo");
    expect(originKindLabel("profile")).toBe("currículo");
    expect(originKindLabel("target")).toBe("sua lista");
    expect(originKindLabel("x")).toBe("—");
  });
});
