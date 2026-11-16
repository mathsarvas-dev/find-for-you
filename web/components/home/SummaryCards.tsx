import Link from "next/link";
import type { Overview } from "@/lib/overview";

const num = new Intl.NumberFormat("pt-BR");

function count(n: number, one: string, many: string): string {
  return `${num.format(n)} ${n === 1 ? one : many}`;
}

type Card = {
  id: string;
  label: string;
  value: string;
  detail: string;
  href: string;
};

function cardsFor({ profile, companies, jobs, matches }: Overview): Card[] {
  const paused = Math.max(0, companies.total - companies.active);
  const sourceDetail =
    companies.total === 0
      ? "Nenhuma empresa cadastrada"
      : [
          companies.auto > 0 ? count(companies.auto, "automática", "automáticas") : "Nenhuma automática",
          ...(paused > 0 ? [count(paused, "pausada", "pausadas")] : []),
        ].join(" · ");

  return [
    {
      id: "profile",
      label: "Perfil",
      value: !profile.confirmed
        ? "Não confirmado"
        : profile.version !== null
          ? `Versão ${num.format(profile.version)}`
          : "Confirmado",
      detail: !profile.confirmed
        ? "Envie o currículo e confirme"
        : profile.provisional
          ? "Provisório (sem extração) · já ranqueia"
          : profile.vectorized
          ? "Confirmado · vetores prontos"
          : "Confirmado · vetores em preparo",
      href: "/onboarding",
    },
    {
      id: "sources",
      label: "Fontes",
      value: count(companies.active, "empresa ativa", "empresas ativas"),
      detail: sourceDetail,
      href: "/sources",
    },
    {
      id: "jobs",
      label: "Vagas",
      value: count(jobs.open, "vaga aberta", "vagas abertas"),
      detail:
        jobs.open > 0
          ? "Coletadas das suas fontes"
          : companies.active > 0
            ? "Aguardando a primeira coleta"
            : "Cadastre empresas para coletar",
      href: "/jobs",
    },
    {
      id: "scored",
      label: "Avaliadas",
      value: `${num.format(matches.scored)} de ${num.format(jobs.open)}`,
      detail:
        matches.pending > 0
          ? `${num.format(matches.pending)} na fila`
          : matches.scored > 0
            ? "Na versão atual do perfil"
            : "Nenhuma vaga avaliada ainda",
      href: "/jobs",
    },
  ];
}

/** Quatro cartões-resumo clicáveis; números em texto simples. */
export function SummaryCards({ overview }: { overview: Overview }) {
  return (
    <ul className="summary-cards">
      {cardsFor(overview).map((card) => (
        <li key={card.id}>
          <Link className="summary-card" href={card.href}>
            <span className="summary-card__label">{card.label}</span>
            <span className="summary-card__value">{card.value}</span>
            <span className="summary-card__detail">{card.detail}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

const LABELS = ["Perfil", "Fontes", "Vagas", "Avaliadas"];

/** Esqueleto dos cartões: mesmo tamanho, sem links nem números. */
export function SummaryCardsLoading() {
  return (
    <ul className="summary-cards">
      {LABELS.map((label) => (
        <li key={label}>
          <div className="summary-card">
            <span className="summary-card__label">{label}</span>
            <span className="summary-card__value">
              <span className="skeleton skeleton--short" aria-hidden="true" />
            </span>
            <span className="summary-card__detail">
              <span className="skeleton" aria-hidden="true" />
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
