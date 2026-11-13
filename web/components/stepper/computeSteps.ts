import type { Overview } from "@/lib/overview";

export type StepState = "done" | "current" | "upcoming" | "blocked" | "optional";

export type Step = {
  id: string;
  title: string;
  detail: string;
  state: StepState;
  cta?: { label: string; href: string };
  reviewHref?: string;
  blockedReason?: string;
};

const num = new Intl.NumberFormat("pt-BR");

function count(n: number, one: string, many: string): string {
  return `${num.format(n)} ${n === 1 ? one : many}`;
}

type Draft = {
  id: string;
  title: string;
  detail: string;
  done: boolean;
  /** "required" entra no cálculo do passo atual; "optional" só se os obrigatórios acabaram. */
  kind: "required" | "optional" | "blocked";
  cta?: Step["cta"];
  reviewHref: string;
  blockedReason?: string;
};

/** Trilha "Seu caminho" a partir dos contadores reais de GET /overview. */
export function computeSteps(o: Overview): Step[] {
  const { profile, interview, companies, jobs, matches } = o;
  const version = profile.version;

  const drafts: Draft[] = [
    {
      id: "profile",
      title: "Montar o perfil",
      detail: profile.confirmed
        ? version !== null ? `Perfil na versão ${version}` : "Perfil montado"
        : "Envie o currículo e revise os dados",
      done: profile.confirmed,
      kind: "required",
      cta: { label: "Montar meu perfil", href: "/onboarding" },
      reviewHref: "/onboarding",
    },
    {
      id: "confirm",
      title: "Confirmar e vetorizar",
      detail: !profile.confirmed
        ? "Confirme o perfil para preparar a busca"
        : profile.vectorized
          ? "Perfil confirmado · vetores prontos"
          : "Perfil confirmado · vetores em preparo",
      done: profile.confirmed && profile.vectorized,
      kind: "required",
      cta: { label: profile.confirmed ? "Ver vetorização" : "Confirmar perfil", href: "/onboarding" },
      reviewHref: "/onboarding",
    },
    {
      id: "interview",
      title: "Entrevista rápida",
      detail:
        interview.state === "done"
          ? "Entrevista concluída"
          : interview.state === "in_progress"
            ? "Opcional · em andamento"
            : "Opcional · afina o perfil com algumas perguntas",
      done: interview.state === "done",
      kind: "optional",
      cta: {
        label: interview.state === "in_progress" ? "Continuar entrevista" : "Fazer a entrevista",
        href: "/onboarding/interview",
      },
      reviewHref: "/onboarding/interview",
    },
    {
      id: "sources",
      title: "Fontes de vagas",
      detail:
        companies.active > 0
          ? `${count(companies.active, "empresa", "empresas")} · ${count(jobs.open, "vaga aberta", "vagas abertas")}`
          : companies.total > 0
            ? `Nenhuma empresa ativa · ${count(companies.total, "pausada", "pausadas")}`
            : "Nenhuma empresa ativa",
      done: companies.active > 0,
      kind: "required",
      cta: { label: "Adicionar empresas", href: "/sources" },
      reviewHref: "/sources",
    },
    {
      id: "jobs",
      title: "Vagas coletadas",
      detail:
        jobs.open > 0
          ? count(jobs.open, "vaga aberta", "vagas abertas")
          : companies.active > 0
            ? "Aguardando a primeira coleta"
            : "Nenhuma vaga coletada ainda",
      done: jobs.open > 0,
      kind: "required",
      // Nada a criar aqui: a coleta roda sozinha; o botão leva às fontes.
      cta: { label: "Ver fontes", href: "/sources" },
      reviewHref: "/sources",
    },
    {
      id: "evaluate",
      title: "Avaliar melhores vagas",
      detail:
        matches.scored > 0
          ? count(matches.scored, "vaga avaliada", "vagas avaliadas") +
            (matches.pending > 0 ? ` · ${num.format(matches.pending)} na fila` : "")
          : matches.pending > 0
            ? `Nenhuma vaga avaliada ainda · ${num.format(matches.pending)} na fila`
            : "Nenhuma vaga avaliada ainda",
      done: matches.scored > 0,
      kind: "required",
      cta: { label: "Avaliar melhores vagas", href: "/jobs" },
      reviewHref: "/jobs",
    },
    {
      id: "apply",
      title: "Candidatar-se",
      detail: "Em breve",
      done: false,
      kind: "blocked",
      reviewHref: "",
      blockedReason: "As candidaturas chegam em uma próxima versão.",
    },
  ];

  const firstRequired = drafts.find((d) => d.kind === "required" && !d.done);
  const current =
    firstRequired ?? drafts.find((d) => d.kind === "optional" && !d.done);

  return drafts.map((d): Step => {
    const base = { id: d.id, title: d.title, detail: d.detail };
    if (d.kind === "blocked") {
      return { ...base, state: "blocked", blockedReason: d.blockedReason };
    }
    if (d.done) return { ...base, state: "done", reviewHref: d.reviewHref };
    if (d === current) return { ...base, state: "current", cta: d.cta };
    if (d.kind === "optional") return { ...base, state: "optional", cta: d.cta };
    return { ...base, state: "upcoming" };
  });
}
