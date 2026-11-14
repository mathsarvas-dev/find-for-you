"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useOverview } from "@/components/shell/OverviewContext";
import { PageHeader } from "@/components/shell/PageHeader";
import { computeSteps, type Step } from "@/components/stepper/computeSteps";
import { Stepper } from "@/components/stepper/Stepper";
import type { Overview } from "@/lib/overview";
import { SummaryCards, SummaryCardsLoading } from "./SummaryCards";

const num = new Intl.NumberFormat("pt-BR");

/** Saudação pela hora local (0-23). Só chamada em efeito: nada de Date no render. */
export function greetingFor(hour: number): string {
  if (hour >= 5 && hour < 12) return "Bom dia";
  if (hour >= 12 && hour < 18) return "Boa tarde";
  return "Boa noite";
}

function firstName(name: string | null | undefined): string | null {
  const first = typeof name === "string" ? name.trim().split(/\s+/)[0] : "";
  return first ? first : null;
}

/** "Bom dia, Ana." calculado depois de montar; null no servidor e no primeiro render. */
function useGreeting(name: string | null | undefined): string | null {
  const [hour, setHour] = useState<number | null>(null);
  useEffect(() => {
    // Hora local só no navegador, depois da hidratação: o pré-render fica estável.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHour(new Date().getHours());
  }, []);
  if (hour === null) return null;
  const who = firstName(name);
  return `${greetingFor(hour)}${who ? `, ${who}` : ""}.`;
}

function statusLine(overview: Overview, current: Step | undefined): string {
  if (current) return `Próximo passo: ${current.title}.`;
  const scored = overview.matches.scored;
  return `Trilha em dia: ${num.format(scored)} ${scored === 1 ? "vaga avaliada" : "vagas avaliadas"}.`;
}

function Lead({ greeting, line }: { greeting: string | null; line: string | null }) {
  return (
    <span className="home__lead">
      {greeting && <span className="home__greeting">{greeting}</span>}
      {greeting && line && " "}
      {line && <span>{line}</span>}
    </span>
  );
}

const SYSTEM_HREF = "/settings#sistema";

function PathSkeleton() {
  return (
    <ol className="home__path-skeleton" aria-hidden="true">
      {[0, 1, 2, 3, 4].map((i) => (
        <li key={i}>
          <span className="home__dot" />
          <span className="skeleton" />
        </li>
      ))}
    </ol>
  );
}

/** Erro de rede sem dados: diz o que falhou, onde ver e um único botão para tentar de novo. */
function LoadError({ onRetry }: { onRetry: () => Promise<void> }) {
  const [retrying, setRetrying] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  function retry() {
    if (retrying) return;
    setRetrying(true);
    onRetry().finally(() => {
      if (mounted.current) setRetrying(false);
    });
  }

  return (
    <div className="home__error">
      <div className="notice notice--flush" role="alert">
        <strong>Não consegui carregar o seu progresso.</strong>
        <p>
          O pedido do resumo à API (<code>GET /overview</code>) falhou. Se a API estiver parada,
          suba-a e tente de novo.
        </p>
        <p>
          <Link href={SYSTEM_HREF}>Ver estado do sistema</Link>
        </p>
      </div>
      <button type="button" className="button" onClick={retry} disabled={retrying}>
        {retrying ? "Tentando…" : "Tentar de novo"}
      </button>
    </div>
  );
}

/**
 * Painel Início: cabeçalho com o CTA do passo atual, trilha "Seu caminho" e
 * cartões-resumo. Lê o overview da casca (useOverview); não busca nada sozinho.
 */
export function JourneyPanel() {
  const { overview, status, refresh } = useOverview();
  const greeting = useGreeting(overview?.profile.name);

  if (overview === null) {
    const failed = status === "down";
    return (
      <>
        <PageHeader title="Início" description={<Lead greeting={greeting} line={null} />} />
        {failed ? (
          <LoadError onRetry={refresh} />
        ) : (
          <div className="home" aria-busy="true">
            <p className="sr-only">Carregando seu progresso…</p>
            <section className="panel home__path" aria-labelledby="seu-caminho">
              <div className="panel__head">
                <h2 id="seu-caminho">Seu caminho</h2>
              </div>
              <div className="home__path-body">
                <PathSkeleton />
              </div>
            </section>
            <section className="home__summary" aria-labelledby="resumo">
              <h2 id="resumo" className="sr-only">
                Resumo
              </h2>
              <SummaryCardsLoading />
            </section>
          </div>
        )}
      </>
    );
  }

  const steps = computeSteps(overview);
  const current = steps.find((s) => s.state === "current");
  const cta = current?.cta ?? { label: "Ver vagas avaliadas", href: "/jobs" };

  return (
    <>
      <PageHeader
        title="Início"
        description={<Lead greeting={greeting} line={statusLine(overview, current)} />}
        actions={
          <Link className="button" href={cta.href}>
            {cta.label}
          </Link>
        }
      />
      {status === "down" && (
        <p className="home__stale" role="status">
          Não consegui atualizar agora; estes são os últimos números recebidos.{" "}
          <Link href={SYSTEM_HREF}>Ver estado do sistema</Link>
        </p>
      )}
      <div className="home">
        <section className="panel home__path" aria-labelledby="seu-caminho">
          <div className="panel__head">
            <h2 id="seu-caminho">Seu caminho</h2>
          </div>
          <div className="home__path-body">
            <Stepper steps={steps} orientation="vertical" label="Seu caminho" />
          </div>
        </section>
        <section className="home__summary" aria-labelledby="resumo">
          <h2 id="resumo" className="sr-only">
            Resumo
          </h2>
          <SummaryCards overview={overview} />
        </section>
      </div>
    </>
  );
}
