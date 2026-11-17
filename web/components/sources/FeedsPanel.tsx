"use client";

import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import {
  addFeed,
  deleteFeed,
  listFeeds,
  parseKeywords,
  type Feed,
  type FeedSourceInfo,
  type FeedsState,
} from "@/lib/feeds";
import { ApiError } from "@/lib/profile";
import { relativeTime, safeJobUrl } from "@/lib/sources";

const CLOCK_MS = 30_000;

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao falar com a API. Tente de novo.";

/** Motivo para a fonte não poder ser usada agora; null quando está pronta. */
export function unavailableReason(s: FeedSourceInfo): string | null {
  if (s.needs_key && !s.key_configured) return "Chave não configurada no .env";
  if (!s.gate_allowed) {
    return s.gate_status === "conditional" ? "Ligue na aba Portais" : "Bloqueado pelo site; só importar por link";
  }
  return null;
}

/** Nome exibido do feed: o da atribuição (o crédito), ou o da fonte cadastrada. */
function labelOf(feed: Feed, sources: FeedSourceInfo[]): string {
  return feed.attribution?.label ?? sources.find((s) => s.source === feed.source)?.label ?? feed.source;
}

/**
 * Feeds de agregadores: cadastro (fonte, palavras-chave, país), estado da coleta e o
 * crédito obrigatório de cada fonte. Nunca mostra o valor de nenhuma chave.
 */
export function FeedsPanel() {
  const [state, setState] = useState<FeedsState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Só renderiza depois de montar (a aba não existe no prerender), então Date.now() é seguro.
  const [now, setNow] = useState(() => Date.now());
  const [source, setSource] = useState("");
  const [keywords, setKeywords] = useState("");
  const [country, setCountry] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const sourceId = useId();
  const keywordsId = useId();
  const keywordsHint = useId();
  const countryId = useId();

  const load = useCallback(async () => {
    try {
      const next = await listFeeds();
      if (mounted.current) {
        setState(next);
        setLoadError(null);
      }
    } catch (err) {
      if (mounted.current) setLoadError(message(err));
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    listFeeds()
      .then((next) => {
        if (mounted.current) setState(next);
      })
      .catch((err) => {
        if (mounted.current) setLoadError(message(err));
      });
    const timer = setInterval(() => setNow(Date.now()), CLOCK_MS);
    return () => {
      mounted.current = false;
      clearInterval(timer);
    };
  }, [load]);

  const sources = state?.sources ?? [];
  const available = sources.filter((s) => unavailableReason(s) === null);
  // A escolha vale só se a fonte ainda está disponível; senão cai na primeira disponível.
  const current =
    available.find((s) => s.source === source)?.source ?? available[0]?.source ?? "";

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (inFlight.current || !current) return;
    const parsed = parseKeywords(keywords);
    if (!parsed.ok) {
      setFormError(parsed.error);
      return;
    }
    const cc = country.trim().toUpperCase();
    if (cc && !/^[A-Z]{2}$/.test(cc)) {
      setFormError("País deve ter 2 letras (ex.: BR) ou ficar em branco.");
      return;
    }
    inFlight.current = true;
    setBusy(true);
    setFormError(null);
    setActionError(null);
    try {
      await addFeed({ source: current, params: { keywords: parsed.keywords, country: cc || null } });
      if (!mounted.current) return;
      setKeywords("");
      setCountry("");
      await load();
    } catch (err) {
      if (mounted.current) setFormError(message(err));
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  async function remove(feed: Feed) {
    if (!window.confirm("Excluir este feed? As vagas já coletadas continuam.")) return;
    try {
      await deleteFeed(feed.id);
      if (mounted.current) {
        setState((prev) => (prev ? { ...prev, feeds: prev.feeds.filter((f) => f.id !== feed.id) } : prev));
      }
    } catch (err) {
      if (mounted.current) setActionError(message(err));
    }
  }

  const feeds = state?.feeds ?? [];

  return (
    <section className="panel" aria-labelledby="feeds-title">
      <div className="panel__head">
        <h2 id="feeds-title">Feeds de agregadores</h2>
        {state && <span className="mono">{feeds.filter((f) => f.active).length} ativos</span>}
      </div>
      <div className="panel__body stack">
        <p className="muted">
          Agregadores reúnem vagas de muitas empresas. Cada feed mostra o crédito da fonte nas vagas que
          traz.
        </p>

        {loadError && (
          <div className="notice" role="alert">
            <strong>Não deu para carregar os feeds.</strong>
            <p>{loadError}</p>
          </div>
        )}
        {!state && !loadError && <p className="muted">Carregando feeds…</p>}

        {state && feeds.length === 0 && <p className="empty">Nenhum feed ainda. Crie o primeiro abaixo.</p>}

        {feeds.length > 0 && (
          <ul className="company-list">
            {feeds.map((f) => {
              const label = labelOf(f, sources);
              const credit = safeJobUrl(f.attribution?.url ?? "");
              return (
                <li key={f.id} className="company" data-active={f.active || undefined}>
                  <div className="company__main">
                    <p className="company__title">
                      <span className="company__name">{label}</span>
                    </p>
                    <p className="company__meta">
                      <span>{f.params.keywords.length > 0 ? f.params.keywords.join(", ") : "Sem palavras-chave"}</span>
                      <span className="mono">{f.params.country ?? "Qualquer país"}</span>
                      <span>{`${f.jobs_seen} ${f.jobs_seen === 1 ? "vaga vista" : "vagas vistas"}`}</span>
                    </p>
                    <p className="company__run">
                      <span>
                        {f.last_run_at === null
                          ? "ainda não coletado"
                          : now > 0
                            ? `última coleta: ${relativeTime(f.last_run_at, now)}`
                            : "última coleta: —"}
                      </span>
                    </p>
                    {f.last_error && <p className="company__failure">Erro na última coleta: {f.last_error}</p>}
                    <p className="company__credit">
                      {credit ? (
                        <a href={credit} target="_blank" rel="noopener noreferrer">
                          Vagas via {label}
                        </a>
                      ) : (
                        <span>Vagas via {label}</span>
                      )}
                    </p>
                  </div>
                  <div className="company__actions">
                    <button
                      type="button"
                      className="link-button"
                      aria-label={`Excluir feed ${label}`}
                      onClick={() => void remove(f)}
                    >
                      Excluir
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {actionError && (
          <p className="field__error" role="alert">
            {actionError}
          </p>
        )}

        {state && (
          <form className="add-feed" noValidate onSubmit={(e) => void submit(e)}>
            <div className="field">
              <label className="field__label" htmlFor={sourceId}>
                Fonte
              </label>
              <select id={sourceId} className="input" value={current} onChange={(e) => setSource(e.target.value)}>
                {sources.map((s) => {
                  const reason = unavailableReason(s);
                  return (
                    <option key={s.source} value={s.source} disabled={reason !== null}>
                      {reason ? `${s.label} (${reason})` : s.label}
                    </option>
                  );
                })}
              </select>
            </div>
            <div className="field">
              <label className="field__label" htmlFor={keywordsId}>
                Palavras-chave (até 5, separadas por vírgula)
              </label>
              <input
                id={keywordsId}
                className="input"
                type="text"
                autoComplete="off"
                maxLength={400}
                value={keywords}
                aria-describedby={keywordsHint}
                onChange={(e) => setKeywords(e.target.value)}
              />
              <p className="field__hint" id={keywordsHint}>
                Cada palavra-chave pode ter até 60 caracteres.
              </p>
            </div>
            <div className="field">
              <label className="field__label" htmlFor={countryId}>
                País (opcional)
              </label>
              <input
                id={countryId}
                className="input mono"
                type="text"
                autoComplete="off"
                maxLength={2}
                placeholder="BR"
                value={country}
                onChange={(e) => setCountry(e.target.value.toUpperCase())}
              />
            </div>
            {formError && (
              <p className="field__error" role="alert">
                {formError}
              </p>
            )}
            <button type="submit" className="button" disabled={busy || !current}>
              Adicionar feed
            </button>
          </form>
        )}
      </div>
    </section>
  );
}
