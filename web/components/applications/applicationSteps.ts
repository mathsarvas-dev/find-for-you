import type { Step } from "@/components/stepper/computeSteps";
import type { ApplicationDetail } from "@/lib/applications";

export type ApplicationView = "read" | "resume" | "questions" | "kit";

const TITLES: Record<ApplicationView, string> = {
  read: "Ler a vaga",
  resume: "Currículo",
  questions: "Perguntas",
  kit: "Kit",
};

/** Trilha da candidatura: Ler a vaga → Currículo → Perguntas → Kit. */
export function applicationSteps(
  d: ApplicationDetail | null,
  view: ApplicationView,
): Step[] {
  const done: Record<ApplicationView, boolean> = {
    read: d?.requirements != null,
    resume: d?.resume?.status === "valid",
    questions: false,
    kit: d?.state === "applied",
  };
  const detail: Record<ApplicationView, string> = {
    read: done.read ? "Requisitos lidos" : "Aguardando a leitura da vaga",
    resume: done.resume ? "Currículo pronto" : "Currículo pendente",
    questions: "Respostas das perguntas da vaga",
    kit: done.kit ? "Candidatura aplicada" : "Kit para revisar e baixar",
  };
  return (Object.keys(TITLES) as ApplicationView[]).map((id) => ({
    id,
    title: TITLES[id],
    detail: detail[id],
    state: done[id] ? "done" : view === id ? "current" : "upcoming",
  }));
}
