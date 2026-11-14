import type { Step } from "@/components/stepper/computeSteps";

const num = new Intl.NumberFormat("pt-BR");

export type JobsStepsInput = {
  /** Há perfil confirmado (profile_version não nulo). */
  hasProfile: boolean;
  vectorized: boolean;
  /** Vagas ranqueadas sob os filtros atuais. */
  total: number;
  /** Vagas carregadas com nota da IA. */
  scored: number;
  /** Vagas carregadas na fila da IA. */
  pending: number;
};

/**
 * Trilha horizontal de /jobs (Filtrar → Avaliar → Ler), derivada do estado que a
 * página já tem (meta da lista e itens carregados).
 */
export function jobsSteps({ hasProfile, vectorized, total, scored, pending }: JobsStepsInput): Step[] {
  const ranked = hasProfile && vectorized && total > 0;
  const evaluated = ranked && scored > 0;

  const filter: Step = !hasProfile
    ? {
        id: "filter",
        title: "Filtrar",
        detail: "País, modelo, senioridade, salário",
        state: "blocked",
        blockedReason: "Confirme um perfil primeiro.",
      }
    : {
        id: "filter",
        title: "Filtrar",
        detail: ranked ? `${num.format(total)} ${total === 1 ? "vaga" : "vagas"} no ranking` : "País, modelo, senioridade, salário",
        state: ranked ? "done" : "current",
      };

  const evaluateDetail = evaluated
    ? `${num.format(scored)} ${scored === 1 ? "vaga avaliada" : "vagas avaliadas"}` +
      (pending > 0 ? ` · ${num.format(pending)} na fila` : "")
    : pending > 0
      ? `${num.format(pending)} na fila da IA`
      : "Nota da IA para as 30 melhores";

  return [
    filter,
    {
      id: "evaluate",
      title: "Avaliar",
      detail: evaluateDetail,
      state: evaluated ? "done" : ranked ? "current" : "upcoming",
    },
    {
      id: "read",
      title: "Ler",
      detail: "Abra uma vaga: nota, pontos fortes e lacunas",
      state: evaluated ? "current" : "upcoming",
    },
  ];
}
