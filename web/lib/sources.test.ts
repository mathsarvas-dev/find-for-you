import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./profile";
import {
  addCompany,
  collectNow,
  getJob,
  isPending,
  listCompanies,
  listJobs,
  patchCompany,
  relativeTime,
  safeJobUrl,
  workModelLabel,
  type Company,
} from "./sources";

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

const company: Company = {
  id: 1,
  source: "greenhouse",
  identifier: "gitlab",
  name: "GitLab",
  country: null,
  active: true,
  last_run_at: null,
  last_success_at: null,
  next_run_at: "2026-11-12T12:00:00Z",
  last_error: null,
  consecutive_failures: 0,
  open_jobs: 0,
  origin: "manual",
};

describe("listCompanies", () => {
  it("GET /companies devolve a lista", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, [company]));
    await expect(listCompanies()).resolves.toEqual([company]);
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/companies");
  });

  it("filtra por origem na query string", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, []));
    await listCompanies("auto");
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/companies?origin=auto");
  });
});

describe("addCompany", () => {
  it("POST /companies com JSON e a URL colada como está", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(201, company));
    await addCompany({
      source: "greenhouse",
      identifier: "https://boards.greenhouse.io/gitlab",
      country: "BR",
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/companies");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      source: "greenhouse",
      identifier: "https://boards.greenhouse.io/gitlab",
      country: "BR",
    });
  });

  it.each([
    [409, "Essa empresa já está cadastrada."],
    [422, "Quadro não encontrado no Greenhouse."],
    [503, "Fonte indisponível no momento."],
    [502, "Resposta inesperada da fonte."],
  ])("%i vira ApiError com o detail do servidor", async (status, detail) => {
    mockFetch(async () => jsonResponse(status, { detail }));
    const err = await addCompany({ source: "greenhouse", identifier: "x" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(status);
    expect(err.message).toBe(detail);
  });

  it("422 de validação (detail em lista) vira mensagem específica", async () => {
    mockFetch(async () => jsonResponse(422, { detail: [{ msg: "x" }] }));
    const err = await addCompany({ source: "greenhouse", identifier: "x" }).catch((e) => e);
    expect(err.status).toBe(422);
    expect(err.message).toMatch(/token do quadro e o país/);
  });

  it("503 sem detail usa mensagem própria", async () => {
    mockFetch(async () => new Response("oops", { status: 503 }));
    const err = await addCompany({ source: "greenhouse", identifier: "x" }).catch((e) => e);
    expect(err.message).toMatch(/Greenhouse não respondeu/);
  });
});

describe("patchCompany / collectNow / getJob", () => {
  it("PATCH /companies/{id} envia só o que mudou", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, { ...company, active: false }));
    await expect(patchCompany(1, { active: false })).resolves.toMatchObject({ active: false });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/companies/1");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ active: false });
  });

  it("collect-now 409 traz a mensagem do servidor", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(409, { detail: "Empresa inativa." }));
    const err = await collectNow(3).catch((e) => e);
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/companies/3/collect-now");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("Empresa inativa.");
  });

  it("getJob busca /jobs/{id}", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, { id: 7 }));
    await getJob(7);
    expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/jobs/7");
  });
});

describe("listJobs", () => {
  it("monta a query string com filtros", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, { total: 0, items: [] }));
    await listJobs({ status: "closed", companyId: 4, limit: 25, offset: 50, q: " react & go " });
    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.pathname).toBe("/jobs");
    expect(url.searchParams.get("status")).toBe("closed");
    expect(url.searchParams.get("company_id")).toBe("4");
    expect(url.searchParams.get("limit")).toBe("25");
    expect(url.searchParams.get("offset")).toBe("50");
    expect(url.searchParams.get("q")).toBe("react & go");
  });

  it("omite empresa e busca vazias e corta a busca em 100 caracteres", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, { total: 0, items: [] }));
    await listJobs({ status: "open", companyId: null, limit: 50, offset: 0, q: "   " });
    let url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.searchParams.has("company_id")).toBe(false);
    expect(url.searchParams.has("q")).toBe(false);
    await listJobs({ status: "all", companyId: null, limit: 50, offset: 0, q: "a".repeat(150) });
    url = new URL(String(fetchMock.mock.calls[1][0]));
    expect(url.searchParams.get("q")).toHaveLength(100);
  });
});

describe("helpers", () => {
  const now = Date.parse("2026-11-12T12:00:00Z");

  it("relativeTime em PT-BR", () => {
    expect(relativeTime("2026-11-12T11:59:40Z", now)).toBe("agora há pouco");
    expect(relativeTime("2026-11-12T11:55:00Z", now)).toBe("há 5 min");
    expect(relativeTime("2026-11-12T09:00:00Z", now)).toBe("há 3 h");
    expect(relativeTime("2026-11-11T09:00:00Z", now)).toBe("há 1 dia");
    expect(relativeTime("2026-11-02T12:00:00Z", now)).toBe("há 10 dias");
    expect(relativeTime("2026-08-01T12:00:00Z", now)).toBe("01/08/2026");
  });

  it("isPending: ativa e nunca coletada ou com coleta vencida", () => {
    expect(isPending(company, now)).toBe(true);
    const done = { ...company, last_run_at: "2026-11-12T11:00:00Z", next_run_at: "2026-11-12T13:00:00Z" };
    expect(isPending(done, now)).toBe(false);
    expect(isPending({ ...done, next_run_at: "2026-11-12T11:59:00Z" }, now)).toBe(true);
    expect(isPending({ ...company, active: false }, now)).toBe(false);
  });

  it("safeJobUrl aceita só http(s)", () => {
    expect(safeJobUrl("https://boards.greenhouse.io/gitlab/jobs/1")).toBe(
      "https://boards.greenhouse.io/gitlab/jobs/1",
    );
    expect(safeJobUrl("http://example.org/x")).toBe("http://example.org/x");
    expect(safeJobUrl("javascript:alert(1)")).toBeNull();
    expect(safeJobUrl(" JAVASCRIPT:alert(1)")).toBeNull();
    expect(safeJobUrl("data:text/html,x")).toBeNull();
    expect(safeJobUrl("não é url")).toBeNull();
  });

  it("workModelLabel", () => {
    expect(workModelLabel("remote")).toBe("remoto");
    expect(workModelLabel("hybrid")).toBe("híbrido");
    expect(workModelLabel("onsite")).toBe("presencial");
    expect(workModelLabel("unknown")).toBe("—");
  });
});
