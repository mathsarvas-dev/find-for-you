import { describe, expect, it } from "vitest";
import type { Company } from "@/lib/sources";
import { sourcesSteps } from "./sourcesSteps";

const company = (patch: Partial<Company> = {}): Company => ({
  id: 1,
  source: "greenhouse",
  identifier: "gitlab",
  name: "GitLab",
  country: null,
  active: true,
  last_run_at: null,
  last_success_at: null,
  next_run_at: null,
  last_error: null,
  consecutive_failures: 0,
  open_jobs: 0,
  origin: "manual",
  collect_mode: "funnel",
  stub_jobs: 0,
  ...patch,
});

const states = (companies: Company[]) => sourcesSteps(companies).map((s) => s.state);

describe("sourcesSteps", () => {
  it("Adicionar → Coletar → Ver vagas", () => {
    expect(sourcesSteps([]).map((s) => s.title)).toEqual(["Adicionar", "Coletar", "Ver vagas"]);
  });

  it("sem empresas: Adicionar é o passo atual", () => {
    expect(states([])).toEqual(["current", "upcoming", "upcoming"]);
  });

  it("com empresa ainda sem coleta bem-sucedida: Coletar é o atual", () => {
    expect(states([company()])).toEqual(["done", "current", "upcoming"]);
    // Uma coleta que falhou não conta como coletada.
    expect(states([company({ last_run_at: "2026-11-12T11:00:00Z", last_error: "404" })])).toEqual([
      "done",
      "current",
      "upcoming",
    ]);
  });

  it("alguma empresa já coletada: Ver vagas é o atual, com o total de vagas abertas", () => {
    const steps = sourcesSteps([
      company({ last_run_at: "2026-11-12T11:00:00Z", last_success_at: "2026-11-12T11:00:00Z", open_jobs: 3 }),
      company({ id: 2, open_jobs: 0 }),
    ]);
    expect(steps.map((s) => s.state)).toEqual(["done", "done", "current"]);
    expect(steps[0].detail).toBe("2 empresas");
    expect(steps[1].detail).toBe("3 vagas abertas");
  });

  it("sem fontes: o detalhe convida a colar quadro, página de carreiras ou criar feed", () => {
    expect(sourcesSteps([])[0].detail).toBe("Cole um quadro, uma página de carreiras ou crie um feed");
  });

  it("nenhum passo traz botão próprio", () => {
    expect(sourcesSteps([company()]).every((s) => s.cta === undefined)).toBe(true);
  });
});
