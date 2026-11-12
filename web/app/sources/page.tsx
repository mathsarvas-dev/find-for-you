"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AddCompanyForm } from "@/components/sources/AddCompanyForm";
import { CompanyRow } from "@/components/sources/CompanyRow";
import { JobsList } from "@/components/sources/JobsList";
import { ApiError } from "@/lib/profile";
import { isPending, listCompanies, type Company } from "@/lib/sources";

const POLL_MS = 10_000;
/**
 * Depois disso com coleta pendente, o worker provavelmente não está rodando.
 * Folgado de propósito: o worker coleta poucas empresas por ciclo de 60 s e
 * divide o laço com a vetorização e o preenchimento das vagas, então uma fila
 * de várias empresas leva alguns minutos mesmo com o worker saudável.
 */
const SLOW_MS = 300_000;
/** Atualiza os tempos relativos ("há 5 min") e percebe coletas que venceram. */
const CLOCK_MS = 30_000;

type Phase =
  | { name: "loading" }
  | { name: "load-error"; message: string }
  | { name: "ready"; companies: Company[] };

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao falar com a API. Tente de novo.";

/** Alguma coleta terminou desde a última leitura (as vagas mudaram). */
function collectedSince(prev: Company[], next: Company[]): boolean {
  const before = new Map(prev.map((c) => [c.id, c.last_run_at]));
  return next.some((c) =>
    before.has(c.id) ? before.get(c.id) !== c.last_run_at : c.last_run_at !== null,
  );
}

export default function SourcesPage() {
  const [phase, setPhase] = useState<Phase>({ name: "loading" });
  const [pollError, setPollError] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);
  const [jobsKey, setJobsKey] = useState(0);
  // Só lido em handlers e efeitos: Date.now() no render quebra o prerender.
  const [now, setNow] = useState(0);
  const mounted = useRef(true);
  const known = useRef<Company[]>([]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  /** Guarda a lista nova; se alguma coleta terminou, recarrega as vagas. */
  const apply = useCallback((companies: Company[]) => {
    if (!mounted.current) return;
    if (collectedSince(known.current, companies)) setJobsKey((k) => k + 1);
    known.current = companies;
    setNow(Date.now());
    setPhase({ name: "ready", companies });
  }, []);

  /** Primeira leitura (ou retentativa depois de erro). Nunca rejeita. */
  const fetchInitial = useCallback(async () => {
    try {
      const companies = await listCompanies();
      known.current = companies;
      apply(companies);
    } catch (err) {
      if (mounted.current) setPhase({ name: "load-error", message: message(err) });
    }
  }, [apply]);

  useEffect(() => {
    // Carga inicial: a API só é alcançável do navegador (sem cache no servidor).
    void fetchInitial();
  }, [fetchInitial]);

  function reload() {
    setPhase({ name: "loading" });
    void fetchInitial();
  }

  /** Relê as empresas agora (empresa nova, retentativa). */
  const refresh = useCallback(async () => {
    try {
      apply(await listCompanies());
    } catch (err) {
      if (mounted.current) setPollError(message(err));
    }
  }, [apply]);

  const companies = phase.name === "ready" ? phase.companies : null;
  const pendingCount = companies && now > 0 ? companies.filter((c) => isPending(c, now)).length : 0;
  const pending = pendingCount > 0;
  const polling = pending && pollError === null;

  // Relógio dos tempos relativos.
  const ready = companies !== null;
  useEffect(() => {
    if (!ready) return;
    const timer = setInterval(() => setNow(Date.now()), CLOCK_MS);
    return () => clearInterval(timer);
  }, [ready]);

  // Consulta a cada 10 s só enquanto alguma coleta está pendente; para num erro de rede
  // (até "Tentar de novo") e ao desmontar.
  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const next = await listCompanies();
        if (cancelled) return;
        apply(next);
        const t = Date.now();
        if (next.some((c) => isPending(c, t))) timer = setTimeout(poll, POLL_MS);
      } catch (err) {
        if (!cancelled) setPollError(message(err));
      }
    };
    timer = setTimeout(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [polling, apply]);

  // 90 s seguidos com coleta pendente: explica a causa provável.
  useEffect(() => {
    if (!pending) return;
    const timer = setTimeout(() => setSlow(true), SLOW_MS);
    return () => {
      clearTimeout(timer);
      setSlow(false);
    };
  }, [pending]);

  function retryPolling() {
    setPollError(null);
    void refresh();
  }

  function replace(updated: Company) {
    if (!companies) return;
    apply(companies.map((c) => (c.id === updated.id ? updated : c)));
  }

  return (
    <main className="page">
      <div className="page-head">
        <h1>Fontes</h1>
        <p>
          As empresas que o Rumo vigia. Cada uma é coletada sozinha, em segundo plano, e as vagas
          novas aparecem na lista abaixo.
        </p>
      </div>

      {phase.name === "loading" && (
        <section className="panel" aria-busy="true" aria-label="Carregando empresas">
          <div className="panel__body stack">
            <p className="muted">Buscando as empresas cadastradas…</p>
            <span className="skeleton" aria-hidden="true" />
          </div>
        </section>
      )}

      {phase.name === "load-error" && (
        <section className="panel">
          <div className="notice notice--top" role="alert">
            <strong>Não deu para carregar as fontes.</strong>
            <p>{phase.message}</p>
          </div>
          <div className="panel__foot">
            <span>
              Suba a API com <code>docker compose up -d api</code> e tente de novo.
            </span>
            <button type="button" className="button" onClick={reload}>
              Tentar de novo
            </button>
          </div>
        </section>
      )}

      {companies && (
        <>
          <section className="panel" aria-labelledby="empresas">
            <div className="panel__head">
              <h2 id="empresas">Empresas vigiadas</h2>
              <span className="mono">
                {companies.length} {companies.length === 1 ? "empresa" : "empresas"} · Greenhouse
              </span>
            </div>
            <div className="panel__body">
              <AddCompanyForm onAdded={() => void refresh()} />
            </div>

            {pollError && (
              <div className="notice sources__notice" role="alert">
                <strong>Perdemos contato com a API.</strong>
                <p>{pollError}</p>
                <div className="actions">
                  <button type="button" className="button button--secondary" onClick={retryPolling}>
                    Tentar de novo
                  </button>
                </div>
              </div>
            )}

            {slow && !pollError && (
              <div className="notice notice--warn" role="status" aria-live="polite">
                <strong>A coleta ainda não começou.</strong>
                <p>
                  O worker pode não estar rodando. Veja <code>docker compose logs worker</code> e,
                  se ele estiver parado, suba com <code>docker compose up -d worker</code>.
                </p>
              </div>
            )}

            {companies.length === 0 ? (
              <div className="sources__empty">
                <p className="empty">
                  Nenhuma empresa cadastrada ainda. Cole acima o token ou a URL do quadro de vagas
                  de uma empresa no Greenhouse; a primeira coleta começa logo depois.
                </p>
              </div>
            ) : (
              <ul className="company-list">
                {companies.map((c) => (
                  <CompanyRow key={c.id} company={c} now={now} onChange={replace} />
                ))}
              </ul>
            )}

            <div className="panel__foot">
              <Link href="/">← Voltar ao início</Link>
              <span aria-live="polite">
                {polling
                  ? `Coleta pendente em ${pendingCount} ${pendingCount === 1 ? "empresa" : "empresas"}. A página confere a cada 10 segundos.`
                  : ""}
              </span>
            </div>
          </section>

          <div className="sources__jobs">
            <JobsList companies={companies} now={now} refreshKey={jobsKey} />
          </div>
        </>
      )}
    </main>
  );
}
