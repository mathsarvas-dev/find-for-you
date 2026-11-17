"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "@/lib/profile";
import {
  listJobs,
  MAX_QUERY_CHARS,
  relativeTime,
  safeJobUrl,
  workModelLabel,
  type Company,
  type JobItem,
  type JobStatusFilter,
} from "@/lib/sources";

export const JOBS_PAGE_SIZE = 50;
const DEBOUNCE_MS = 300;

type JobsListProps = {
  companies: Company[];
  /** Relógio do cliente; 0 antes de montar. */
  now: number;
  /** Muda quando a página quer recarregar a primeira página (coleta terminou, empresa nova). */
  refreshKey: number;
};

type Phase = { name: "loading" } | { name: "error"; message: string } | { name: "ready" };

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao falar com a API. Tente de novo.";

const countText = (n: number) => `${n} ${n === 1 ? "vaga" : "vagas"}`;

/** Lista somente leitura das vagas coletadas. Títulos e locais vêm da fonte: só texto. */
export function JobsList({ companies, now, refreshKey }: JobsListProps) {
  const [status, setStatus] = useState<JobStatusFilter>("open");
  const [companyId, setCompanyId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [retry, setRetry] = useState(0);
  const [phase, setPhase] = useState<Phase>({ name: "loading" });
  const [items, setItems] = useState<JobItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  /** Só a resposta da requisição mais recente vale (filtros podem mudar rápido). */
  const latest = useRef(0);
  const companyFieldId = useId();
  const statusFieldId = useId();
  const searchFieldId = useId();

  // Busca com debounce: só consulta depois de 300 ms sem digitar.
  useEffect(() => {
    if (search === q) return;
    const timer = setTimeout(() => setQ(search), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search, q]);

  // Primeira página: a cada mudança de filtro, retentativa ou recarga pedida pela página.
  useEffect(() => {
    const id = ++latest.current;
    let cancelled = false;
    const stale = () => cancelled || id !== latest.current;
    listJobs({ status, companyId, limit: JOBS_PAGE_SIZE, offset: 0, q })
      .then((page) => {
        if (stale()) return;
        setItems(page.items);
        setTotal(page.total);
        setMoreError(null);
        setLoadingMore(false);
        setPhase({ name: "ready" });
      })
      .catch((err) => {
        if (stale()) return;
        setLoadingMore(false);
        setPhase({ name: "error", message: message(err) });
      });
    return () => {
      // Desmontou ou os filtros mudaram: a resposta em voo fica obsoleta.
      cancelled = true;
    };
  }, [status, companyId, q, retry, refreshKey]);

  async function loadMore() {
    if (loadingMore) return;
    const id = ++latest.current;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await listJobs({ status, companyId, limit: JOBS_PAGE_SIZE, offset: items.length, q });
      if (id !== latest.current) return;
      setItems((prev) => [...prev, ...page.items]);
      setTotal(page.total);
    } catch (err) {
      if (id === latest.current) setMoreError(message(err));
    } finally {
      if (id === latest.current) setLoadingMore(false);
    }
  }

  const filtered = status !== "open" || companyId !== null || q !== "";
  const hasMore = items.length < total;

  return (
    <section className="panel" aria-labelledby="vagas-coletadas">
      <div className="panel__head">
        <h2 id="vagas-coletadas">Vagas coletadas</h2>
        <span className="mono" aria-live="polite">
          {phase.name === "ready" ? countText(total) : ""}
        </span>
      </div>

      <div className="jobs__filters">
        <div className="field">
          <label className="field__label" htmlFor={companyFieldId}>
            Empresa
          </label>
          <select
            id={companyFieldId}
            className="input"
            value={companyId === null ? "" : String(companyId)}
            onChange={(e) => setCompanyId(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">Todas as empresas</option>
            {companies.map((c) => (
              <option key={c.id} value={String(c.id)}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="field__label" htmlFor={statusFieldId}>
            Situação
          </label>
          <select
            id={statusFieldId}
            className="input"
            value={status}
            onChange={(e) => setStatus(e.target.value as JobStatusFilter)}
          >
            <option value="open">abertas</option>
            <option value="closed">encerradas</option>
            <option value="all">todas</option>
          </select>
        </div>
        <div className="field jobs__search">
          <label className="field__label" htmlFor={searchFieldId}>
            Buscar no título
          </label>
          <input
            id={searchFieldId}
            className="input"
            type="search"
            maxLength={MAX_QUERY_CHARS}
            placeholder="Ex.: dados, backend, produto"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>

      {phase.name === "loading" && (
        <div className="panel__body stack" aria-busy="true" aria-label="Carregando vagas">
          <span className="skeleton" aria-hidden="true" />
          <span className="skeleton skeleton--short" aria-hidden="true" />
        </div>
      )}

      {phase.name === "error" && (
        <>
          <div className="notice notice--top" role="alert">
            <strong>Não deu para carregar as vagas.</strong>
            <p>{phase.message}</p>
          </div>
          <div className="panel__foot">
            <span>Confira se a API está no ar.</span>
            <button type="button" className="button" onClick={() => setRetry((n) => n + 1)}>
              Tentar de novo
            </button>
          </div>
        </>
      )}

      {phase.name === "ready" && items.length === 0 && (
        <div className="panel__body">
          <p className="empty">
            {filtered
              ? "Nenhuma vaga com esses filtros. Mude a empresa, a situação ou a busca."
              : "Nenhuma vaga ainda — cadastre uma empresa e aguarde a primeira coleta"}
          </p>
        </div>
      )}

      {phase.name === "ready" && items.length > 0 && (
        <>
          <table className="jobs">
            <thead>
              <tr>
                <th scope="col">Título</th>
                <th scope="col">Empresa</th>
                <th scope="col">Local</th>
                <th scope="col">Modelo</th>
                <th scope="col">Publicada</th>
                <th scope="col">
                  <span className="sr-only">Link</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((job) => (
                <JobRow key={job.id} job={job} now={now} />
              ))}
            </tbody>
          </table>
          {(hasMore || moreError) && (
            <div className="panel__foot">
              <span className="mono">
                {items.length} de {total}
              </span>
              {moreError && (
                <span className="field__error jobs__more-error" role="alert">
                  {moreError}
                </span>
              )}
              {hasMore && (
                <button
                  type="button"
                  className="button button--secondary"
                  disabled={loadingMore}
                  aria-busy={loadingMore || undefined}
                  onClick={() => void loadMore()}
                >
                  Mais vagas
                </button>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

/** Crédito obrigatório de vaga vinda de agregador: "via <fonte>" linkando a fonte. */
function FeedCredit({ attribution }: { attribution: { label: string; url: string } }) {
  const href = safeJobUrl(attribution.url);
  const text = `via ${attribution.label}`;
  return (
    <span className="jobs__credit">
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {text}
        </a>
      ) : (
        text
      )}
    </span>
  );
}

function JobRow({ job, now }: { job: JobItem; now: number }) {
  const href = safeJobUrl(job.url);
  return (
    <tr>
      <td className="jobs__title" data-label="Título">
        {job.title}
      </td>
      <td data-label="Empresa">
        {job.company_name}
        {job.origin === "feed" && job.attribution && <FeedCredit attribution={job.attribution} />}
      </td>
      <td className="jobs__muted" data-label="Local">
        {job.location_text || "—"}
      </td>
      <td data-label="Modelo">{workModelLabel(job.work_model)}</td>
      <td className="mono jobs__date" data-label="Publicada">
        {job.posted_at && now > 0 ? relativeTime(job.posted_at, now) : "—"}
      </td>
      <td className="jobs__link">
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Abrir vaga — ${job.title}`}
          >
            Abrir vaga
          </a>
        ) : (
          <span className="muted">link indisponível</span>
        )}
      </td>
    </tr>
  );
}
