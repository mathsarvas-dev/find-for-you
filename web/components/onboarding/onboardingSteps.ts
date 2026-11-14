import type { Step, StepState } from "@/components/stepper/computeSteps";

/** Onde o fluxo do perfil está, derivado da fase local da página /onboarding. */
export type OnboardingStage = "upload" | "reading" | "review" | "vectorizing" | "ready";

/** Índice do passo atual (0..3) para cada estágio. */
const CURRENT: Record<OnboardingStage, number> = {
  upload: 0,
  reading: 1,
  review: 1,
  vectorizing: 2,
  ready: 3,
};

/**
 * Trilha horizontal do perfil: Enviar currículo → Revisar → Confirmar → Entrevista.
 * Sem botões nos passos: as ações ficam no cabeçalho e nos painéis da página.
 */
export function onboardingSteps(stage: OnboardingStage): Step[] {
  const current = CURRENT[stage];
  const reading = stage === "reading";
  const state = (i: number): StepState => (i < current ? "done" : i === current ? "current" : "upcoming");
  const details = [
    current > 0 ? "Currículo enviado" : "PDF ou DOCX, até 5 MB",
    current > 1 ? "Dados conferidos" : reading ? "Lendo o currículo…" : "Confira o que foi lido",
    current > 2 ? "Perfil confirmado" : stage === "vectorizing" ? "Preparando os vetores…" : "Salva o perfil",
    "Opcional · completa o perfil",
  ];
  const titles = ["Enviar currículo", "Revisar", "Confirmar", "Entrevista"];
  const ids = ["upload", "review", "confirm", "interview"];
  return titles.map((title, i) => ({ id: ids[i], title, detail: details[i], state: state(i) }));
}
