import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./profile";
import {
  DEFAULT_FILTERS,
  defaultFilters,
  formatSalary,
  getMatch,
  getSearchProfile,
  hasPending,
  isDefaultFilters,
  listMatches,
  putSearchProfile,
  refreshMatches,
  scoreLabel,
  type MatchItem,
} from "./matching";

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

function item(extra: Partial<MatchItem> = {}): MatchItem {
  return {
    job: {
      id: 1,
      company_id: 1,
      company_name: "GitLab",
      title: "Engenheira de dados",
      url: "https://boards.greenhouse.io/gitlab/jobs/1",
      location_text: "Remote",
      country: "BR",
      city: null,
      work_model: "remote",
      seniority: "senior",
      language: "en",
      department: "Data",
      salary_known: false,
      status: "open",
      first_seen_at: "2026-11-12T11:00:00Z",
      posted_at: null,
      salary_min: null,
      salary_max: null,
      salary_currency: null,
      salary_period: null,
    },
    vec_score: 0.784,
    ai_score: null,
    state: null,
    unknown_fields: ["salary"],
    ...extra,
  };
}

describe("exclusões e consulta", () => {
  it("DEFAULT_FILTERS traz as exclusões vazias e isDefaultFilters as considera", () => {
    for (const k of [
      "exclude_countries",
      "exclude_work_models",
      "exclude_seniorities",
      "exclude_languages",
      "exclude_departments",
      "title_exclude",
    ] as const) {
      expect(DEFAULT_FILTERS[k]).toEqual([]);
      expect(isDefaultFilters({ ...DEFAULT_FILTERS, [k]: ["x"] })).toBe(false);
    }
  });

  it("defaultFilters devolve arrays novos e DEFAULT_FILTERS é congelado", () => {
    const a = defaultFilters();
    a.countries.push("BR");
    expect(defaultFilters().countries).toEqual([]);
    expect(Object.isFrozen(DEFAULT_FILTERS)).toBe(true);
    expect(Object.isFrozen(DEFAULT_FILTERS.countries)).toBe(true);
  });

  it("PUT só envia query_text quando o argumento é passado", async () => {
    const body = { filters: DEFAULT_FILTERS, query_text: null, query_pending: false };
    const fn = mockFetch(async () => jsonResponse(200, body));
    await putSearchProfile(DEFAULT_FILTERS);
    expect("query_text" in JSON.parse(String(fn.mock.calls[0][1]?.body))).toBe(false);
    await putSearchProfile(DEFAULT_FILTERS, null);
    expect(JSON.parse(String(fn.mock.calls[1][1]?.body)).query_text).toBeNull();
    await putSearchProfile(DEFAULT_FILTERS, "dados");
    expect(JSON.parse(String(fn.mock.calls[2][1]?.body)).query_text).toBe("dados");
  });
});

describe("search profile", () => {
  it("GET /search-profile devolve o perfil completo", async () => {
    const profile = { filters: DEFAULT_FILTERS, query_text: "dados", query_pending: true };
    const fetchMock = mockFetch(async () => jsonResponse(200, profile));
    await expect(getSearchProfile()).resolves.toEqual(profile);
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/search-profile");
  });

  it("PUT /search-profile manda os filtros como corpo", async () => {
    const filters = { ...DEFAULT_FILTERS, countries: ["BR"], salary_min: 100000, salary_currency: "BRL" };
    const profile = { filters, query_text: null, query_pending: false };
    const fetchMock = mockFetch(async () => jsonResponse(200, profile));
    await expect(putSearchProfile(filters)).resolves.toEqual(profile);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/search-profile");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual(filters);
  });

  it("422 de validação vira mensagem específica", async () => {
    mockFetch(async () => jsonResponse(422, { detail: [{ msg: "x" }] }));
    const err = await putSearchProfile(DEFAULT_FILTERS).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.message).toMatch(/países como BR/);
  });
});

describe("listMatches", () => {
  it("monta a query string", async () => {
    const fetchMock = mockFetch(async () =>
      jsonResponse(200, { total: 0, profile_version: 1, vectorized: true, missing_embeddings: 0, chunks_used: 3, items: [] }),
    );
    await listMatches({ status: "open", limit: 50, offset: 100 });
    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.pathname).toBe("/matches");
    expect(url.searchParams.get("status")).toBe("open");
    expect(url.searchParams.get("limit")).toBe("50");
    expect(url.searchParams.get("offset")).toBe("100");
  });

  it("erro com detail em texto vira ApiError com a mensagem do servidor", async () => {
    mockFetch(async () => jsonResponse(409, { detail: "Os filtros salvos são inválidos. Salve-os novamente." }));
    const err = await listMatches({ status: "open", limit: 50, offset: 0 }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.message).toBe("Os filtros salvos são inválidos. Salve-os novamente.");
  });
});

describe("getMatch / refreshMatches", () => {
  it("GET /matches/{id}", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, { id: 7 }));
    await getMatch(7);
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/matches/7");
  });

  it("POST /matches/refresh; 409 e 429 trazem o detail", async () => {
    const fetchMock = mockFetch(async () =>
      jsonResponse(200, { requested: 30, created: 30, reset: 0, already_ok: 0, profile_version: 2 }),
    );
    await expect(refreshMatches()).resolves.toMatchObject({ requested: 30 });
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/matches/refresh");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");

    mockFetch(async () => jsonResponse(409, { detail: "Confirme seu perfil antes de avaliar vagas." }));
    let err = await refreshMatches().catch((e) => e);
    expect(err.status).toBe(409);
    expect(err.message).toBe("Confirme seu perfil antes de avaliar vagas.");

    mockFetch(async () => new Response("", { status: 429 }));
    err = await refreshMatches().catch((e) => e);
    expect(err.status).toBe(429);
    expect(err.message).toMatch(/Aguarde alguns segundos/);
  });
});

describe("helpers", () => {
  it("hasPending só com estado pendente", () => {
    expect(hasPending([])).toBe(false);
    expect(hasPending([item(), item({ state: "ok", ai_score: 80 })])).toBe(false);
    expect(hasPending([item(), item({ state: "pendente" })])).toBe(true);
  });

  it("scoreLabel: nota da IA quando houver; senão vetorial com legenda", () => {
    expect(scoreLabel(item({ ai_score: 82, state: "ok" }))).toEqual({
      value: 82,
      source: "ai",
      caption: "nota da IA",
    });
    expect(scoreLabel(item({ vec_score: 0.784 }))).toEqual({
      value: 78,
      source: "vector",
      caption: "pré-seleção",
    });
    expect(scoreLabel({ ai_score: 0, vec_score: 0.9 })).toMatchObject({ value: 0, source: "ai" });
    expect(scoreLabel({ ai_score: null, vec_score: 1.4 })).toMatchObject({ value: 100 });
    expect(scoreLabel({ ai_score: null, vec_score: null })).toBeNull();
  });

  it("formatSalary", () => {
    expect(
      formatSalary({ salary_known: true, salary_min: 8000, salary_max: 12000, salary_currency: "BRL", salary_period: "month" }),
    ).toBe("BRL 8.000–12.000/mês");
    expect(
      formatSalary({ salary_known: true, salary_min: 120000, salary_max: null, salary_currency: "EUR", salary_period: "year" }),
    ).toBe("EUR 120.000/ano");
    expect(
      formatSalary({ salary_known: false, salary_min: null, salary_max: null, salary_currency: null, salary_period: null }),
    ).toBeNull();
  });

  it("isDefaultFilters", () => {
    expect(isDefaultFilters(DEFAULT_FILTERS)).toBe(true);
    expect(isDefaultFilters({ ...DEFAULT_FILTERS, include_unknown: false })).toBe(true);
    expect(isDefaultFilters({ ...DEFAULT_FILTERS, work_models: ["remote"] })).toBe(false);
    expect(isDefaultFilters({ ...DEFAULT_FILTERS, salary_min: 1, salary_currency: "BRL" })).toBe(false);
  });
});
