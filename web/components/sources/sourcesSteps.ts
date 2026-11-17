import type { Step } from "@/components/stepper/computeSteps";
import type { Company } from "@/lib/sources";

const num = new Intl.NumberFormat("pt-BR");

const count = (n: number, one: string, many: string) => `${num.format(n)} ${n === 1 ? one : many}`;

/**
 * Trilha horizontal de /sources (Adicionar → Coletar → Ver vagas), derivada da lista
 * de empresas que a página já tem. "Coletada" = ao menos uma coleta bem-sucedida.
 */
export function sourcesSteps(companies: Company[]): Step[] {
  const added = companies.length > 0;
  const collected = companies.some((c) => c.last_success_at !== null);
  const open = companies.reduce((sum, c) => sum + c.open_jobs, 0);
  const anyActive = companies.some((c) => c.active);

  return [
    {
      id: "add",
      title: "Adicionar",
      detail: added ? count(companies.length, "empresa", "empresas") : "Cole um quadro, uma página de carreiras ou crie um feed",
      state: added ? "done" : "current",
    },
    {
      id: "collect",
      title: "Coletar",
      detail: collected
        ? count(open, "vaga aberta", "vagas abertas")
        : added && !anyActive
          ? "Nenhuma empresa ativa"
          : "Roda sozinha, em segundo plano",
      state: collected ? "done" : added ? "current" : "upcoming",
    },
    {
      id: "jobs",
      title: "Ver vagas",
      detail: "Ranking contra o seu perfil",
      state: collected ? "current" : "upcoming",
    },
  ];
}
