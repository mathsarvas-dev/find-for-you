import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./profile";
import { DEFAULT_FILTERS, type SearchFilters } from "./matching";
import { filterChips, mergeProposal, parseFilters, removeChip, type ParsedQuery } from "./chatFilters";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
function mockFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}
afterEach(() => vi.unstubAllGlobals());

const f = (extra: Partial<SearchFilters> = {}): SearchFilters => ({ ...DEFAULT_FILTERS, ...extra });
const proposal = (extra: Partial<SearchFilters> = {}, query: string | null = null): ParsedQuery => ({
  filters: f(extra),
  query_text: query,
  notes: [],
});

describe("filterChips", () => {
  it("sem filtros e sem consulta devolve []", () => {
    expect(filterChips(DEFAULT_FILTERS, null)).toEqual([]);
  });

  it("um chip por valor, com os rótulos em PT-BR", () => {
    const chips = filterChips(
      f({
        work_models: ["remote"],
        seniorities: ["senior"],
        languages: ["en"],
        countries: ["BR"],
        exclude_work_models: ["onsite"],
        title_exclude: ["plantão"],
        exclude_departments: ["DE"],
        salary_min: 120000,
        salary_currency: "BRL",
      }),
      "dados",
    );
    const labels = chips.map((c) => c.label);
    expect(labels).toEqual(
      expect.arrayContaining([
        "remoto",
        "sênior",
        "inglês",
        "BR",
        "sem presencial",
        "sem plantão",
        "sem DE",
        "gosto: dados",
      ]),
    );
    expect(labels.some((l) => /^salário ≥ BRL 120\.000\/ano$/.test(l))).toBe(true);
    expect(chips).toHaveLength(9);
    for (const c of chips.filter((c) => c.kind === "exclude")) expect(c.label.startsWith("sem ")).toBe(true);
    expect(new Set(chips.map((c) => c.key)).size).toBe(chips.length);
  });

  it("termo hostil vira rótulo literal", () => {
    const evil = "<img src=x onerror=alert(1)>";
    const chips = filterChips(f({ title_exclude: [evil] }), null);
    expect(chips[0].label).toBe(`sem ${evil}`);
    expect(chips[0].value).toBe(evil);
  });
});

describe("chips duplicados", () => {
  it("um chip por valor equivalente e removeChip tira todas as ocorrências", () => {
    const filters = f({ countries: ["BR", "br", "PT"], departments: ["Data", "data"] });
    const chips = filterChips(filters, null);
    expect(chips).toHaveLength(3);
    expect(new Set(chips.map((c) => c.key)).size).toBe(3);
    const br = chips.find((c) => c.value === "BR")!;
    expect(removeChip(filters, null, br).filters.countries).toEqual(["PT"]);
    const dep = chips.find((c) => c.field === "departments")!;
    expect(removeChip(filters, null, dep).filters.departments).toEqual([]);
  });
});

describe("removeChip", () => {
  it("remove só o termo de title_exclude", () => {
    const filters = f({ title_exclude: ["plantão", "estágio"] });
    const chip = filterChips(filters, null).find((c) => c.label === "sem plantão")!;
    const out = removeChip(filters, null, chip);
    expect(out.filters.title_exclude).toEqual(["estágio"]);
  });

  it("remove a consulta", () => {
    const chip = filterChips(DEFAULT_FILTERS, "dados")[0];
    expect(chip.label).toBe("gosto: dados");
    expect(removeChip(DEFAULT_FILTERS, "dados", chip).queryText).toBeNull();
  });

  it("remover salário zera mínimo e moeda", () => {
    const filters = f({ salary_min: 1000, salary_currency: "BRL" });
    const chip = filterChips(filters, null).find((c) => c.kind === "salary")!;
    const out = removeChip(filters, "x", chip);
    expect(out.filters.salary_min).toBeNull();
    expect(out.filters.salary_currency).toBeNull();
    expect(out.queryText).toBe("x");
  });

  it("remove valor de inclusão sem mexer nos outros", () => {
    const filters = f({ countries: ["BR", "PT"] });
    const chip = filterChips(filters, null).find((c) => c.value === "PT")!;
    expect(removeChip(filters, null, chip).filters.countries).toEqual(["BR"]);
  });
});

describe("mergeProposal", () => {
  it("valor incluído pela frase sai da exclusão atual", () => {
    const out = mergeProposal(f({ exclude_work_models: ["remote"] }), null, proposal({ work_models: ["remote"] }));
    expect(out.filters.work_models).toContain("remote");
    expect(out.filters.exclude_work_models).not.toContain("remote");
  });

  it("valor excluído pela frase sai da inclusão atual", () => {
    const out = mergeProposal(f({ countries: ["DE", "BR"] }), null, proposal({ exclude_countries: ["DE"] }));
    expect(out.filters.countries).toEqual(["BR"]);
    expect(out.filters.exclude_countries).toEqual(["DE"]);
  });

  it("une listas sem duplicata e respeita máximos", () => {
    const cur = f({ countries: ["BR", "PT"] });
    const out = mergeProposal(cur, null, proposal({ countries: ["PT", "DE"] }));
    expect(out.filters.countries).toEqual(["BR", "PT", "DE"]);
    const many = Array.from({ length: 15 }, (_, i) => `t${i}`);
    const out2 = mergeProposal(
      f({ title_exclude: many }),
      null,
      proposal({ title_exclude: ["a", "b", "c", "d", "e", "f", "g"] }),
    );
    expect(out2.filters.title_exclude).toHaveLength(10);
    const deps = Array.from({ length: 25 }, (_, i) => `d${i}`);
    expect(mergeProposal(f(), null, proposal({ departments: deps })).filters.departments).toHaveLength(20);
  });

  it("teto: valores da frase têm prioridade e saem os atuais mais antigos", () => {
    const old = Array.from({ length: 10 }, (_, i) => `t${i}`);
    const a = mergeProposal(f({ title_exclude: old }), null, proposal({ title_exclude: ["plantão"] }));
    expect(a.filters.title_exclude).toHaveLength(10);
    expect(a.filters.title_exclude).toContain("plantão");
    expect(a.filters.title_exclude).not.toContain("t0");
    expect(a.filters.title_exclude.slice(0, 9)).toEqual(old.slice(1));

    const twenty = Array.from({ length: 20 }, (_, i) => `C${i}`);
    const b = mergeProposal(f({ countries: twenty }), null, proposal({ countries: ["DE"] }));
    expect(b.filters.countries).toHaveLength(20);
    expect(b.filters.countries).toContain("DE");

    const c = mergeProposal(
      f({ countries: twenty, exclude_countries: ["DE"] }),
      null,
      proposal({ countries: ["DE"] }),
    );
    expect(c.filters.countries).toContain("DE");
    expect(c.filters.countries).toHaveLength(20);
    expect(c.filters.exclude_countries).not.toContain("DE");

    const big = Array.from({ length: 30 }, (_, i) => `D${i}`);
    const d = mergeProposal(f(), null, proposal({ departments: big, title_exclude: big }));
    expect(d.filters.departments).toEqual(big.slice(0, 20));
    expect(d.filters.title_exclude).toEqual(big.slice(0, 10));
  });

  it("departamentos comparam sem diferenciar caixa", () => {
    const out = mergeProposal(f({ exclude_departments: ["Sales"] }), null, proposal({ departments: ["sales"] }));
    expect(out.filters.exclude_departments).toEqual([]);
    expect(out.filters.departments).toEqual(["sales"]);
  });

  it("salário da proposta vence quando não nulo; senão mantém o atual", () => {
    const cur = f({ salary_min: 50000, salary_currency: "BRL" });
    const out = mergeProposal(cur, null, proposal({ salary_min: 3000, salary_currency: "EUR" }));
    expect(out.filters.salary_min).toBe(3000);
    expect(out.filters.salary_currency).toBe("EUR");
    const keep = mergeProposal(cur, null, proposal());
    expect(keep.filters.salary_min).toBe(50000);
    expect(keep.filters.salary_currency).toBe("BRL");
  });

  it("booleanos ficam os atuais", () => {
    const out = mergeProposal(
      f({ include_unknown: false, accept_no_salary: false }),
      null,
      proposal({ include_unknown: true, accept_no_salary: true }),
    );
    expect(out.filters.include_unknown).toBe(false);
    expect(out.filters.accept_no_salary).toBe(false);
  });

  it("query_text da proposta vence; null mantém o atual", () => {
    expect(mergeProposal(f(), "velho", proposal({}, "novo")).queryText).toBe("novo");
    expect(mergeProposal(f(), "velho", proposal({}, null)).queryText).toBe("velho");
  });

  it("nunca produz conflito entre inclusão e exclusão", () => {
    const cur = f({ countries: ["BR"], exclude_seniorities: ["senior"], languages: ["en"] });
    const p = proposal({ exclude_countries: ["BR"], seniorities: ["senior"], exclude_languages: ["en"] });
    const { filters } = mergeProposal(cur, null, p);
    for (const k of ["countries", "work_models", "seniorities", "languages", "departments"] as const) {
      const ex = filters[`exclude_${k}` as keyof SearchFilters] as string[];
      for (const v of filters[k] as string[]) expect(ex).not.toContain(v);
    }
  });
});

describe("parseFilters", () => {
  const body = { filters: DEFAULT_FILTERS, query_text: "dados", notes: ["ok"] };

  it("POST /filters/parse com {text}", async () => {
    const fetchMock = mockFetch(async () => jsonResponse(200, body));
    await expect(parseFilters("remoto")).resolves.toEqual(body);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/filters/parse");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ text: "remoto" });
  });

  it("422 sem detalhe usa a mensagem padrão", async () => {
    mockFetch(async () => jsonResponse(422, {}));
    const err = await parseFilters("x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("Não consegui transformar essa frase em filtros.");
  });

  it("422 com detalhe, 429 e 503 propagam o texto do servidor", async () => {
    mockFetch(async () => jsonResponse(422, { detail: "Frase muito longa." }));
    expect((await parseFilters("x").catch((e) => e)).message).toBe("Frase muito longa.");
    mockFetch(async () => jsonResponse(429, { detail: "Calma, espere." }));
    expect((await parseFilters("x").catch((e) => e)).message).toBe("Calma, espere.");
    mockFetch(async () => jsonResponse(503, { detail: "IA fora do ar." }));
    expect((await parseFilters("x").catch((e) => e)).message).toBe("IA fora do ar.");
  });
});
