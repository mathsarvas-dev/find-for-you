import { describe, expect, it } from "vitest";
import type { Step } from "@/components/stepper/computeSteps";
import { onboardingSteps, type OnboardingStage } from "./onboardingSteps";

const states = (steps: Step[]) => steps.map((s) => s.state);

describe("onboardingSteps", () => {
  it("quatro passos na ordem do fluxo", () => {
    expect(onboardingSteps("upload").map((s) => s.title)).toEqual([
      "Enviar currículo",
      "Revisar",
      "Confirmar",
      "Entrevista",
    ]);
  });

  const cases: [OnboardingStage, Step["state"][]][] = [
    ["upload", ["current", "upcoming", "upcoming", "upcoming"]],
    ["reading", ["done", "current", "upcoming", "upcoming"]],
    ["review", ["done", "current", "upcoming", "upcoming"]],
    ["vectorizing", ["done", "done", "current", "upcoming"]],
    ["ready", ["done", "done", "done", "current"]],
  ];

  it.each(cases)("%s: estados dos passos", (stage, expected) => {
    expect(states(onboardingSteps(stage))).toEqual(expected);
  });

  it("nenhum passo traz botão próprio: as ações ficam no cabeçalho", () => {
    for (const [stage] of cases) {
      expect(onboardingSteps(stage).every((s) => s.cta === undefined && s.reviewHref === undefined)).toBe(true);
    }
  });
});
