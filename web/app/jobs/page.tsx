"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { FilterBar } from "@/components/jobs/FilterBar";
import { jobsSteps } from "@/components/jobs/jobsSteps";
import { MatchList } from "@/components/jobs/MatchList";
import { MatchPanel, type PanelState } from "@/components/jobs/MatchPanel";
import { useRefreshOverview } from "@/components/shell/OverviewContext";
import { PageHeader } from "@/components/shell/PageHeader";
import { Stepper } from "@/components/stepper/Stepper";
import {
  DEFAULT_FILTERS,
  getMatch,
  getSearchProfile,
  hasPending,
  isDefaultFilters,
  listMatches,
  putSearchProfile,
  refreshMatches,
  type MatchItem,
  type MatchPage,
  type RefreshResult,
  type SearchFilters,
} from "@/lib/matching";
import { ApiError } from "@/lib/profile";

const MATCHES_PAGE_SIZE = 50;
const POLL_MS = 10_000;
/** Limite da API para `limit`. */
const MAX_LIMIT = 100;

type Phase =
  | { name: "loading" }
  | { name: "error"; message: string; status: number | null }
  | { name: "ready" };
type Meta = Omit<MatchPage, "items">;
type Flash = { kind: "ok" | "error"; text: string };

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao falar com a API. Tente de novo.";

const statusOf = (err: unknown) => (err instanceof ApiError ? err.status : null);

const countText = (n: number) => `${n} ${n === 1 ? "vaga" : "vagas"}`;

function refreshText(r: RefreshResult): string {
  if (r.requested === 0) return "Nenhuma vaga para avaliar com esses filtros.";
  const queued = r.requested - r.already_ok;
  if (queued === 0) return `As ${r.requested} melhores vagas já têm nota da IA.`;
  const head = `${countText(queued)} na fila da IA`;
  return r.already_ok > 0 ? `${head}; ${r.already_ok} já tinham nota.` : `${head}.`;
}

/** Teclas de atalho não valem enquanto a pessoa digita. */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

export default function JobsPage() {
  const [phase, setPhase] = useState<Phase>({ name: "loading" });
  const [bootKey, setBootKey] = useState(0);
  const [filters, setFilters] = useState<SearchFilters | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [items, setItems] = useState<MatchItem[]>([]);
  const [saving, setSaving] = useState(false);
  /** Edição de filtro esperando o debounce do FilterBar. */
  const [editing, setEditing] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [panel, setPanel] = useState<PanelState>({ name: "empty" });
  const [detailKey, setDetailKey] = useState(0);
  // Só lido em handlers e efeitos: Date.now() no render quebra o prerender.
  const [now, setNow] = useState(0);
  /** Só a resposta da requisição de lista mais recente vale. */
  const latest = useRef(0);
  /** Só o salvamento de filtros mais recente controla "salvando…". */
  const saveSeq = useRef(0);
  const mounted = useRef(true);
  const itemsRef = useRef<MatchItem[]>([]);
  /** Seleção atual para o atalho de teclado (várias teclas antes do próximo render). */
  const selectedRef = useRef<number | null>(null);
  const refreshOverview = useRefreshOverview();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const apply = useCallback((page: MatchPage) => {
    const { items: next, ...rest } = page;
    itemsRef.current = next;
    setItems(next);
    setMeta(rest);
    setNow(Date.now());
  }, []);

  // Carga inicial (e "Tentar de novo"): filtros salvos e primeira página.
  useEffect(() => {
    const id = ++latest.current;
    let cancelled = false;
    Promise.all([
      getSearchProfile(),
      listMatches({ status: "open", limit: MATCHES_PAGE_SIZE, offset: 0 }),
    ])
      .then(([saved, page]) => {
        if (cancelled || id !== latest.current) return;
        setFilters(saved);
        apply(page);
        setPhase({ name: "ready" });
      })
      .catch((err) => {
        if (cancelled || id !== latest.current) return;
        setPhase({ name: "error", message: message(err), status: statusOf(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [bootKey, apply]);

  function reboot() {
    setPhase({ name: "loading" });
    setPollError(null);
    setSaveError(null);
    setEditing(false);
    setBootKey((k) => k + 1);
  }

  /** Relê a primeira página; descarta respostas de requisições anteriores. */
  const reloadList = useCallback(async () => {
    const id = ++latest.current;
    try {
      const page = await listMatches({ status: "open", limit: MATCHES_PAGE_SIZE, offset: 0 });
      if (!mounted.current || id !== latest.current) return;
      apply(page);
      setMoreError(null);
    } catch (err) {
      if (!mounted.current || id !== latest.current) return;
      setPhase({ name: "error", message: message(err), status: statusOf(err) });
    }
  }, [apply]);

  /** FilterBar já esperou 300 ms: salva o perfil de busca e recarrega a lista. */
  const saveFilters = useCallback(
    async (next: SearchFilters) => {
      const saveId = ++saveSeq.current;
      // Listas pedidas antes deste salvamento usam filtros velhos: descarta.
      ++latest.current;
      setSaving(true);
      setSaveError(null);
      try {
        const saved = await putSearchProfile(next);
        if (!mounted.current || saveId !== saveSeq.current) return;
        setFilters(saved);
        await reloadList();
      } catch (err) {
        if (mounted.current && saveId === saveSeq.current) setSaveError(message(err));
      } finally {
        // Sempre limpa, mesmo se outra recarga da lista correu no meio do PUT.
        if (mounted.current && saveId === saveSeq.current) setSaving(false);
      }
    },
    [reloadList],
  );

  /** 409 de filtros salvos inválidos: grava os padrões e recomeça. */
  async function restoreDefaults() {
    setRestoring(true);
    try {
      await putSearchProfile(DEFAULT_FILTERS);
      if (!mounted.current) return;
      reboot();
    } catch (err) {
      if (mounted.current) setPhase({ name: "error", message: message(err), status: statusOf(err) });
    } finally {
      if (mounted.current) setRestoring(false);
    }
  }

  async function loadMore() {
    if (loadingMore) return;
    const id = latest.current;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await listMatches({ status: "open", limit: MATCHES_PAGE_SIZE, offset: items.length });
      if (!mounted.current || id !== latest.current) return;
      const seen = new Set(itemsRef.current.map((i) => i.job.id));
      const merged = [...itemsRef.current, ...page.items.filter((i) => !seen.has(i.job.id))];
      apply({ ...page, items: merged });
    } catch (err) {
      if (mounted.current && id === latest.current) setMoreError(message(err));
    } finally {
      if (mounted.current) setLoadingMore(false);
    }
  }

  async function evaluate() {
    setRefreshing(true);
    setFlash(null);
    try {
      const result = await refreshMatches();
      // Vagas na fila da IA mudam os contadores da casca.
      refreshOverview();
      if (!mounted.current) return;
      setFlash({ kind: "ok", text: refreshText(result) });
      setPollError(null);
      await reloadList();
    } catch (err) {
      if (mounted.current) setFlash({ kind: "error", text: message(err) });
    } finally {
      if (mounted.current) setRefreshing(false);
    }
  }

  // Polling a cada 10 s só enquanto alguma vaga estiver "pendente"; para num erro e ao desmontar.
  const pending = hasPending(items);
  const polling = pending && pollError === null;
  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      // Não invalida outras requisições: se um filtro mudar no meio, esta resposta é descartada.
      const id = latest.current;
      const limit = Math.min(MAX_LIMIT, Math.max(MATCHES_PAGE_SIZE, itemsRef.current.length));
      try {
        const page = await listMatches({ status: "open", limit, offset: 0 });
        if (cancelled || id !== latest.current) {
          if (!cancelled) timer = setTimeout(poll, POLL_MS);
          return;
        }
        // Mantém as linhas além do limite (carregadas com "Mais vagas").
        const seen = new Set(page.items.map((i) => i.job.id));
        const tail = itemsRef.current.slice(limit).filter((i) => !seen.has(i.job.id));
        const merged = [...page.items, ...tail];
        apply({ ...page, items: merged });
        if (hasPending(merged)) timer = setTimeout(poll, POLL_MS);
        // A fila da IA esvaziou: as notas novas entram nos contadores da casca.
        else refreshOverview();
      } catch (err) {
        if (!cancelled) setPollError(message(err));
      }
    };
    timer = setTimeout(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [polling, apply, refreshOverview]);

  // Detalhe da vaga selecionada; relido quando a avaliação dela muda (polling).
  const selected = items.find((i) => i.job.id === selectedId);
  const selectedVersion = selected ? `${selected.state}:${selected.ai_score}` : "";
  useEffect(() => {
    if (selectedId === null) return;
    let cancelled = false;
    getMatch(selectedId)
      .then((detail) => {
        if (!cancelled) setPanel({ name: "ready", detail });
      })
      .catch((err) => {
        if (!cancelled) setPanel({ name: "error", message: message(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, selectedVersion, detailKey]);

  function select(jobId: number) {
    if (jobId === selectedId) return;
    setPanel({ name: "loading" });
    setSelectedId(jobId);
  }

  function closePanel() {
    const id = selectedId;
    setSelectedId(null);
    setPanel({ name: "empty" });
    if (id !== null) document.querySelector<HTMLElement>(`[data-job-id="${id}"]`)?.focus();
  }

  function retryDetail() {
    setPanel({ name: "loading" });
    setDetailKey((k) => k + 1);
  }

  // Atalhos: j/k navegam; s (salvar, marco 6) e p (marco 5) estão reservados e não fazem nada.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return;
      if (e.key === "Escape") {
        const open = selectedRef.current;
        if (open === null) return;
        selectedRef.current = null;
        setSelectedId(null);
        setPanel({ name: "empty" });
        document.querySelector<HTMLElement>(`[data-job-id="${open}"]`)?.focus();
        return;
      }
      if (e.key !== "j" && e.key !== "k") return;
      const list = itemsRef.current;
      if (list.length === 0) return;
      e.preventDefault();
      const index = list.findIndex((i) => i.job.id === selectedRef.current);
      const nextIndex =
        index === -1 ? 0 : Math.min(list.length - 1, Math.max(0, index + (e.key === "j" ? 1 : -1)));
      const id = list[nextIndex].job.id;
      if (id !== selectedRef.current) {
        selectedRef.current = id;
        setPanel({ name: "loading" });
        setSelectedId(id);
      }
      document.querySelector<HTMLElement>(`[data-job-id="${id}"]`)?.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    selectedRef.current = selectedId;
  }, [selectedId]);

  const hasProfile = meta !== null && meta.profile_version !== null;
  // Com filtro não salvo, a avaliação usaria os filtros antigos do servidor.
  const canEvaluate =
    meta !== null &&
    meta.profile_version !== null &&
    meta.vectorized &&
    !refreshing &&
    !saving &&
    !editing;
  const pendingCount = items.filter((i) => i.state === "pendente").length;
  const hasMore = meta !== null && items.length < meta.total;
  const ready = phase.name === "ready" && meta !== null && filters !== null;

  /** Abre o painel de filtros (se recolhido) pelo próprio botão dele e leva o foco até lá. */
  function openFilters() {
    const toggle = document.querySelector<HTMLButtonElement>(".filters__toggle");
    if (!toggle) return;
    if (toggle.getAttribute("aria-expanded") !== "true") toggle.click();
    toggle.focus();
  }

  return (
    <main className="page page--wide">
      <PageHeader
        title="Vagas"
        description="Ordenadas pela nota contra o seu perfil confirmado. A IA avalia as 30 melhores; as outras mostram a pré-seleção vetorial, em cinza."
        actions={
          ready && (
            <>
              <button
                type="button"
                className="button"
                disabled={!canEvaluate}
                aria-busy={refreshing || undefined}
                onClick={() => void evaluate()}
              >
                Avaliar melhores vagas
              </button>
              {hasProfile && (
                <button type="button" className="button button--secondary" onClick={openFilters}>
                  Filtros
                </button>
              )}
            </>
          )
        }
        stepper={
          ready &&
          meta && (
            <Stepper
              steps={jobsSteps({
                hasProfile,
                vectorized: meta.vectorized,
                total: meta.total,
                scored: items.filter((i) => i.ai_score !== null).length,
                pending: pendingCount,
              })}
              orientation="horizontal"
              label="Etapas das vagas"
            />
          )
        }
      />

      {phase.name === "loading" && (
        <section className="panel" aria-busy="true" aria-label="Carregando vagas">
          <div className="panel__body stack">
            <p className="muted">Buscando os filtros salvos e o ranking…</p>
            <span className="skeleton" aria-hidden="true" />
            <span className="skeleton skeleton--short" aria-hidden="true" />
          </div>
        </section>
      )}

      {phase.name === "error" && (
        <section className="panel">
          <div className="notice notice--top" role="alert">
            <strong>Não deu para carregar as vagas.</strong>
            <p>{phase.message}</p>
          </div>
          <div className="panel__foot">
            {phase.status === null ? (
              <span>
                Suba a API com <code>docker compose up -d api</code> e tente de novo.
              </span>
            ) : phase.status === 409 ? (
              <span>Os filtros guardados não valem mais. Restaure os padrões e ajuste de novo.</span>
            ) : (
              <span>
                A API está no ar, mas recusou o pedido. Veja <code>docker compose logs api</code>.
              </span>
            )}
            {phase.status === 409 ? (
              <button
                type="button"
                className="button"
                disabled={restoring}
                aria-busy={restoring || undefined}
                onClick={() => void restoreDefaults()}
              >
                Restaurar filtros padrão
              </button>
            ) : (
              <button type="button" className="button" onClick={reboot}>
                Tentar de novo
              </button>
            )}
          </div>
        </section>
      )}

      {phase.name === "ready" && meta && filters && (
        <>
          {hasProfile && (
            <FilterBar
              initial={filters}
              onChange={(f) => void saveFilters(f)}
              status={saving ? "salvando…" : undefined}
              error={saveError}
              onEditing={setEditing}
            />
          )}

          <div className="matches" data-open={selectedId !== null || undefined}>
            <section className="panel matches__list" aria-labelledby="ranking">
              <div className="panel__head">
                <h2 id="ranking">Ranking</h2>
                <span className="mono" aria-live="polite">
                  {meta.vectorized ? countText(meta.total) : ""}
                </span>
              </div>

              <div className="matches__toolbar">
                {flash ? (
                  <p
                    className={flash.kind === "error" ? "field__error matches__flash" : "matches__flash"}
                    role={flash.kind === "error" ? "alert" : "status"}
                  >
                    {flash.text}
                  </p>
                ) : (
                  <p className="matches__flash muted">
                    “Avaliar melhores vagas” manda as 30 melhores pelos filtros atuais para a nota da IA.
                  </p>
                )}
              </div>

              {pollError && (
                <div className="notice notice--top" role="alert">
                  <strong>Perdemos contato com a API.</strong>
                  <p>{pollError}</p>
                  <div className="actions matches__notice-actions">
                    <button
                      type="button"
                      className="button button--secondary"
                      onClick={() => {
                        setPollError(null);
                        void reloadList();
                      }}
                    >
                      Tentar de novo
                    </button>
                  </div>
                </div>
              )}

              <EmptyState meta={meta} filters={filters} empty={items.length === 0} onRecheck={reboot} />

              {meta.vectorized && items.length > 0 && (
                <>
                  {meta.missing_embeddings > 0 && (
                    <p className="matches__missing">
                      {countText(meta.missing_embeddings)} ainda sem vetor{" "}
                      {meta.missing_embeddings === 1 ? "fica" : "ficam"} fora do ranking.
                    </p>
                  )}
                  <MatchList items={items} selectedId={selectedId} onSelect={select} />
                </>
              )}

              {(hasMore || moreError || polling) && (
                <div className="panel__foot">
                  <span aria-live="polite">
                    {polling
                      ? `Avaliando ${countText(pendingCount)}. A lista confere a cada 10 segundos.`
                      : `${items.length} de ${meta.total}`}
                  </span>
                  {moreError && (
                    <span className="field__error matches__more-error" role="alert">
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
            </section>

            {hasProfile && (
              <aside className="panel matches__panel" aria-label="Detalhes da vaga">
                <MatchPanel
                  state={panel}
                  chunksUsed={meta.chunks_used}
                  now={now}
                  onClose={closePanel}
                  onRetry={retryDetail}
                />
              </aside>
            )}
          </div>
        </>
      )}
    </main>
  );
}

function EmptyState({
  meta,
  filters,
  empty,
  onRecheck,
}: {
  meta: Meta;
  filters: SearchFilters;
  empty: boolean;
  onRecheck: () => void;
}) {
  if (meta.profile_version === null) {
    return (
      <div className="matches__empty">
        <strong>Você ainda não confirmou um perfil.</strong>
        <p>O ranking compara cada vaga com o perfil confirmado. Sem ele, não há o que ordenar.</p>
        <Link className="button button--secondary" href="/onboarding">
          Montar meu perfil
        </Link>
      </div>
    );
  }
  if (!meta.vectorized) {
    return (
      <div className="matches__empty">
        <strong>Seu perfil ainda está sendo vetorizado.</strong>
        <p>
          O worker gera um vetor por trecho do perfil (versão {meta.profile_version}). Quando terminar,
          as vagas aparecem aqui ordenadas pela nota.
        </p>
        <button type="button" className="button button--secondary" onClick={onRecheck}>
          Verificar de novo
        </button>
      </div>
    );
  }
  if (!empty) return null;
  if (isDefaultFilters(filters)) {
    return (
      <div className="matches__empty">
        <strong>Nenhuma vaga ranqueada ainda.</strong>
        <p>
          {meta.missing_embeddings > 0
            ? `${countText(meta.missing_embeddings)} coletadas ainda esperam o vetor; elas entram no ranking quando o worker terminar.`
            : "Nenhuma vaga aberta foi coletada. Cadastre empresas em Fontes e aguarde a primeira coleta."}
        </p>
        <Link className="button button--secondary" href="/sources">
          Cadastrar empresas
        </Link>
      </div>
    );
  }
  return (
    <div className="matches__empty">
      <strong>Nenhuma vaga passa nesses filtros.</strong>
      <p>
        Tire um país, modelo ou senioridade, baixe o salário mínimo ou ligue “Incluir desconhecidos”.
        {meta.missing_embeddings > 0 &&
          ` ${countText(meta.missing_embeddings)} sob esses filtros ainda esperam o vetor.`}
      </p>
    </div>
  );
}
