import { describe, expect, it } from "vitest";
import { jobsSteps, type JobsStepsInput } from "./jobsSteps";

const base: JobsStepsInput = { hasProfile: true, vectorized: true, total: 10, scored: 0, pending: 0 };
const states = (patch: Partial<JobsStepsInput>) =>
  jobsSteps({ ...base, ...patch }).map((s) => s.state);

describe("jobsSteps", () => {
  it("Filtrar → Avaliar → Ler", () => {
    expect(jobsSteps(base).map((s) => s.title)).toEqual(["Filtrar", "Avaliar", "Ler"]);
  });

  it("sem perfil: Filtrar bloqueado com o motivo", () => {
    const steps = jobsSteps({ ...base, hasProfile: false, vectorized: false, total: 0 });
    expect(steps.map((s) => s.state)).toEqual(["blocked", "upcoming", "upcoming"]);
    expect(steps[0].blockedReason).toMatch(/perfil/);
  });

  it("perfil sem vetores ou nenhuma vaga ranqueada: Filtrar é o atual", () => {
    expect(states({ vectorized: false, total: 0 })).toEqual(["current", "upcoming", "upcoming"]);
    expect(states({ total: 0 })).toEqual(["current", "upcoming", "upcoming"]);
  });

  it("vagas ranqueadas sem nota da IA: Avaliar é o atual", () => {
    expect(states({})).toEqual(["done", "current", "upcoming"]);
  });

  it("só vagas na fila da IA: Avaliar continua atual, com a fila no detalhe", () => {
    const steps = jobsSteps({ ...base, pending: 4 });
    expect(steps.map((s) => s.state)).toEqual(["done", "current", "upcoming"]);
    expect(steps[1].detail).toBe("4 na fila da IA");
  });

  it("alguma vaga com nota: Ler é o atual", () => {
    const steps = jobsSteps({ ...base, scored: 2, pending: 1 });
    expect(steps.map((s) => s.state)).toEqual(["done", "done", "current"]);
    expect(steps[1].detail).toBe("2 vagas avaliadas · 1 na fila");
  });

  it("nenhum passo traz botão próprio", () => {
    expect(jobsSteps(base).every((s) => s.cta === undefined)).toBe(true);
  });
});
