import { describe, expect, it } from "vitest";
import { emptyOverview, type Overview } from "@/lib/overview";
import { computeSteps, type Step } from "./computeSteps";

type Patch = { [K in keyof Overview]?: Partial<Overview[K]> };

function ov(patch: Patch = {}): Overview {
  const base = emptyOverview();
  return {
    profile: { ...base.profile, ...patch.profile },
    interview: { ...base.interview, ...patch.interview },
    companies: { ...base.companies, ...patch.companies },
    jobs: { ...base.jobs, ...patch.jobs },
    matches: { ...base.matches, ...patch.matches },
  };
}

const ready: Patch = {
  profile: { confirmed: true, version: 2, vectorized: true },
};

function byId(steps: Step[], id: string): Step {
  const s = steps.find((x) => x.id === id);
  if (!s) throw new Error(`sem passo ${id}`);
  return s;
}

function currentIds(steps: Step[]) {
  return steps.filter((s) => s.state === "current").map((s) => s.id);
}

describe("computeSteps", () => {
  it("devolve os 7 passos na ordem da spec", () => {
    const steps = computeSteps(ov());
    expect(steps.map((s) => s.id)).toEqual([
      "profile", "confirm", "interview", "sources", "jobs", "evaluate", "apply",
    ]);
    expect(steps.map((s) => s.title)).toEqual([
      "Montar o perfil",
      "Confirmar e vetorizar",
      "Entrevista rápida",
      "Fontes de vagas",
      "Vagas coletadas",
      "Avaliar melhores vagas",
      "Candidatar-se",
    ]);
  });

  it("test_fresh_user_current_is_profile", () => {
    const steps = computeSteps(ov());
    expect(currentIds(steps)).toEqual(["profile"]);
    expect(byId(steps, "profile").cta).toEqual({ label: "Montar meu perfil", href: "/onboarding" });
    expect(byId(steps, "confirm").state).toBe("upcoming");
    expect(byId(steps, "interview").state).toBe("optional");
    expect(byId(steps, "interview").cta).toEqual({ label: "Fazer a entrevista", href: "/onboarding/interview" });
    expect(byId(steps, "sources").state).toBe("upcoming");
    expect(byId(steps, "apply").state).toBe("blocked");
  });

  it("test_profile_confirmed_not_vectorized_current_is_confirm", () => {
    const steps = computeSteps(ov({ profile: { confirmed: true, version: 1, vectorized: false } }));
    expect(byId(steps, "profile").state).toBe("done");
    expect(byId(steps, "profile").reviewHref).toBe("/onboarding");
    expect(currentIds(steps)).toEqual(["confirm"]);
    expect(byId(steps, "confirm").cta?.href).toBe("/onboarding");
  });

  it("test_interview_optional_not_blocking_sources", () => {
    for (const state of ["none", "in_progress"] as const) {
      const steps = computeSteps(ov({ ...ready, interview: { state } }));
      expect(byId(steps, "interview").state).toBe("optional");
      expect(currentIds(steps)).toEqual(["sources"]);
    }
    const none = computeSteps(ov({ ...ready, interview: { state: "none" } }));
    expect(byId(none, "interview").cta?.label).toBe("Fazer a entrevista");
    const going = computeSteps(ov({ ...ready, interview: { state: "in_progress" } }));
    expect(byId(going, "interview").cta?.label).toBe("Continuar entrevista");
    const done = computeSteps(ov({ ...ready, interview: { state: "done" } }));
    expect(byId(done, "interview").state).toBe("done");
    expect(byId(done, "interview").reviewHref).toBe("/onboarding/interview");
  });

  it("test_no_companies_current_is_sources_with_cta", () => {
    const steps = computeSteps(ov(ready));
    expect(currentIds(steps)).toEqual(["sources"]);
    expect(byId(steps, "sources").cta).toEqual({ label: "Adicionar empresas", href: "/sources" });
    expect(byId(steps, "jobs").state).toBe("upcoming");
    expect(byId(steps, "jobs").cta).toBeUndefined();
  });

  it("empresas só pausadas não concluem as fontes", () => {
    const steps = computeSteps(ov({ ...ready, companies: { total: 3, active: 0 } }));
    expect(currentIds(steps)).toEqual(["sources"]);
  });

  it("test_companies_but_no_jobs_waits_collection", () => {
    const steps = computeSteps(ov({ ...ready, companies: { total: 2, active: 2 } }));
    expect(byId(steps, "sources").state).toBe("done");
    const jobs = byId(steps, "jobs");
    expect(jobs.state).toBe("current");
    expect(jobs.detail).toBe("Aguardando a primeira coleta");
    expect(jobs.cta).toEqual({ label: "Ver fontes", href: "/sources" });
  });

  it("test_jobs_without_scores_current_is_evaluate", () => {
    const steps = computeSteps(ov({ ...ready, companies: { total: 1, active: 1 }, jobs: { open: 7 } }));
    expect(byId(steps, "jobs").state).toBe("done");
    expect(currentIds(steps)).toEqual(["evaluate"]);
    expect(byId(steps, "evaluate").cta).toEqual({ label: "Avaliar melhores vagas", href: "/jobs" });
  });

  it("test_all_done_apply_blocked", () => {
    const steps = computeSteps(ov({
      ...ready,
      interview: { state: "done" },
      companies: { total: 1, active: 1 },
      jobs: { open: 7 },
      matches: { scored: 3 },
    }));
    expect(currentIds(steps)).toEqual([]);
    expect(steps.filter((s) => s.id !== "apply").every((s) => s.state === "done")).toBe(true);
    const apply = byId(steps, "apply");
    expect(apply.state).toBe("blocked");
    expect(apply.detail).toMatch(/em breve/i);
    expect(apply.blockedReason).toBeTruthy();
    expect(apply.cta).toBeUndefined();
  });

  it("obrigatórios prontos e entrevista pendente: a entrevista vira o atual", () => {
    const steps = computeSteps(ov({
      ...ready,
      interview: { state: "in_progress" },
      companies: { total: 1, active: 1 },
      jobs: { open: 7 },
      matches: { scored: 3 },
    }));
    expect(currentIds(steps)).toEqual(["interview"]);
    expect(byId(steps, "interview").cta?.label).toBe("Continuar entrevista");
    expect(byId(steps, "apply").state).toBe("blocked");
  });

  it("test_details_show_real_numbers", () => {
    const steps = computeSteps(ov({
      profile: { confirmed: true, version: 3, vectorized: true },
      companies: { total: 14, active: 12, auto: 4 },
      jobs: { open: 340 },
      matches: { scored: 25, pending: 5 },
    }));
    expect(byId(steps, "profile").detail).toContain("versão 3");
    expect(byId(steps, "sources").detail).toBe("12 empresas · 340 vagas abertas");
    expect(byId(steps, "jobs").detail).toBe("340 vagas abertas");
    expect(byId(steps, "evaluate").detail).toBe("25 vagas avaliadas · 5 na fila");
  });

  it("singular e milhar em PT-BR", () => {
    const steps = computeSteps(ov({
      ...ready,
      companies: { total: 1, active: 1 },
      jobs: { open: 1 },
      matches: { scored: 1 },
    }));
    expect(byId(steps, "sources").detail).toBe("1 empresa · 1 vaga aberta");
    expect(byId(steps, "evaluate").detail).toBe("1 vaga avaliada");
    const big = computeSteps(ov({ ...ready, companies: { active: 2 }, jobs: { open: 1340 } }));
    expect(byId(big, "jobs").detail).toBe("1.340 vagas abertas");
  });

  it("só passos atuais têm cta visível na trilha; concluídos têm reviewHref", () => {
    const steps = computeSteps(ov({ ...ready, companies: { total: 1, active: 1 } }));
    for (const s of steps) {
      if (s.state === "done") expect(s.reviewHref).toBeTruthy();
      else expect(s.reviewHref).toBeUndefined();
    }
  });
});
