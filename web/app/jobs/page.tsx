"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ChatBar } from "@/components/jobs/ChatBar";
import { FilterBar } from "@/components/jobs/FilterBar";
import { FilterChips } from "@/components/jobs/FilterChips";
import { jobsSteps } from "@/components/jobs/jobsSteps";
import { ProfileSwitcher } from "@/components/jobs/ProfileSwitcher";
import { MatchList } from "@/components/jobs/MatchList";
import { MatchPanel, type PanelState } from "@/components/jobs/MatchPanel";
import { useRefreshOverview } from "@/components/shell/OverviewContext";
import { PageHeader } from "@/components/shell/PageHeader";
import { Stepper } from "@/components/stepper/Stepper";
import {
  defaultFilters,
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
import { mergeProposal, removeChip, type Chip, type ParsedQuery } from "@/lib/chatFilters";
import { createApplication, prepareApplication } from "@/lib/applications";
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

/** Filtros e consulta como a página os quer (pedidos) ou como o servidor confirmou. */
type Intent = { filters: SearchFilters; queryText: string | null };

/** Aba visível: o polling pausa com a aba oculta e volta quando ela reaparece. */
function subscribeVisibility(onChange: () => void) {
  if (typeof document === "undefined") return () => {};
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}
const isVisible = () => typeof document === "undefined" || document.visibilityState !== "hidden";
const serverVisible = () => true;

/** Teclas de atalho não valem enquanto a pessoa digita. */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

export default function JobsPage() {
  const [phase, setPhase] = useState<Phase>({ name: "loading" });
  const [bootKey, setBootKey] = useState(0);
  /** Fonte única dos filtros salvos: chips, chat e painel manual leem daqui. */
  const [filters, setFilters] = useState<SearchFilters | null>(null);
  const [queryText, setQueryText] = useState<string | null>(null);
  /** A consulta semântica ainda não tem vetor (o worker vetoriza a cada 15 s). */
  const [queryPending, setQueryPending] = useState(false);
  /** Sobe quando os filtros mudam por fora do painel manual (chat, chips): ele redesenha. */
  const [revision, setRevision] = useState(0);
  /** O chat está gravando uma proposta: a barra não aceita outra frase até terminar. */
  const [applyingProposal, setApplyingProposal] = useState(false);
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
  const [preparing, setPreparing] = useState(false);
  const [prepareError, setPrepareError] = useState<string | null>(null);
  const [savingJob, setSavingJob] = useState(false);
  const [saveNote, setSaveNote] = useState<{ ok: boolean; text: string } | null>(null);
  const router = useRouter();
  const prepareRef = useRef<() => void>(() => {});
  const saveRef = useRef<() => void>(() => {});
  // Só lido em handlers e efeitos: Date.now() no render quebra o prerender.
  const [now, setNow] = useState(0);
  /** Só a resposta da requisição de lista mais recente vale. */
  const latest = useRef(0);
  /** Só o salvamento de filtros mais recente controla "salvando…". */
  const saveSeq = useRef(0);
  const mounted = useRef(true);
  const itemsRef = useRef<MatchItem[]>([]);
  /**
   * Últimos filtros/consulta PEDIDOS (atualizado antes de cada PUT): base de toda ação nova,
   * para duas ações seguidas não partirem do mesmo estado salvo e uma apagar a outra.
   */
  const intended = useRef<Intent | null>(null);
  /** Últimos filtros/consulta confirmados pelo servidor (para voltar quando um PUT falha). */
  const confirmed = useRef<Intent | null>(null);
  /** Salvamento mais recente: um salvamento substituído responde com o resultado dele. */
  const lastSave = useRef<Promise<boolean> | null>(null);
  const chatInputRef = useRef<HTMLInputElement>(null);
  const visible = useSyncExternalStore(subscribeVisibility, isVisible, serverVisible);
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
    // A consulta entrou no ranking: o vetor dela ficou pronto.
    if (page.query_used) setQueryPending(false);
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
        const base = { filters: saved.filters, queryText: saved.query_text };
        intended.current = base;
        confirmed.current = base;
        setFilters(saved.filters);
        setQueryText(saved.query_text);
        setQueryPending(saved.query_pending);
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

  /** Mostra um estado de filtros (pedido ou confirmado) nos chips e, se `resync`, no painel. */
  const show = useCallback((state: Intent, resync: boolean) => {
    setFilters(state.filters);
    setQueryText(state.queryText);
    if (resync) {
      // O painel redesenha e descarta o rascunho (e o debounce que ele esperava).
      setRevision((r) => r + 1);
      setEditing(false);
    }
  }, []);

  /**
   * O único caminho de gravação dos filtros (painel manual, chat e chips): salva o perfil de
   * busca e recarrega a lista. `queryText` omitido preserva a consulta; null a apaga.
   * `external` (chat, chips, consulta): o painel manual redesenha ANTES do PUT, para que um
   * rascunho velho dele não reverta a mudança. O salvamento do próprio painel nunca o redesenha.
   * Devolve true só se a mudança valeu: um salvamento substituído devolve o resultado do mais
   * novo (que partiu deste, via `intended`); uma falha devolve false.
   */
  const saveFilters = useCallback(
    (next: SearchFilters, queryText?: string | null, external = false): Promise<boolean> => {
      const saveId = ++saveSeq.current;
      const want: Intent = {
        filters: next,
        queryText: queryText === undefined ? (intended.current?.queryText ?? null) : queryText,
      };
      intended.current = want;
      show(want, external);
      // Listas pedidas antes deste salvamento usam filtros velhos: descarta.
      ++latest.current;
      setSaving(true);
      setSaveError(null);
      const superseded = () => saveId !== saveSeq.current;
      const run = (async () => {
        try {
          const saved =
            queryText === undefined ? await putSearchProfile(next) : await putSearchProfile(next, queryText);
          if (!mounted.current) return false;
          // Resposta fora de ordem: só a do salvamento mais recente atualiza o estado.
          if (superseded()) return (await lastSave.current) ?? false;
          const state = { filters: saved.filters, queryText: saved.query_text };
          confirmed.current = state;
          intended.current = state;
          show(state, false);
          setQueryPending(saved.query_pending);
          await reloadList();
          return true;
        } catch (err) {
          if (!mounted.current) return false;
          if (superseded()) return (await lastSave.current) ?? false;
          setSaveError(message(err));
          // Volta ao que o servidor tem; o painel só redesenha se a mudança veio de fora dele.
          if (confirmed.current) {
            intended.current = confirmed.current;
            show(confirmed.current, external);
          }
          return false;
        } finally {
          // Sempre limpa, mesmo se outra recarga da lista correu no meio do PUT.
          if (mounted.current && !superseded()) setSaving(false);
        }
      })();
      lastSave.current = run;
      return run;
    },
    [reloadList, show],
  );

  /** "Aplicar" do chat: funde a proposta (a frase vence) e grava pelo mesmo caminho do painel. */
  async function applyProposal(p: ParsedQuery) {
    const base = intended.current;
    if (!base) return;
    const merged = mergeProposal(base.filters, base.queryText, p);
    setApplyingProposal(true);
    let ok = false;
    try {
      ok = await saveFilters(merged.filters, merged.queryText, true);
    } finally {
      if (mounted.current) setApplyingProposal(false);
    }
    // Rejeitar mantém a proposta na barra de chat; o erro aparece no painel de filtros.
    if (!ok) throw new Error("save failed");
  }

  function removeFilterChip(chip: Chip) {
    const base = intended.current;
    if (!base) return;
    const next = removeChip(base.filters, base.queryText, chip);
    void saveFilters(next.filters, next.queryText, true);
  }

  function editQuery(text: string | null): Promise<boolean> {
    const base = intended.current;
    if (!base) return Promise.resolve(true);
    return saveFilters(base.filters, text, true);
  }

  const focusChat = useCallback(() => chatInputRef.current?.focus(), []);

  /** 409 de filtros salvos inválidos: grava os padrões e recomeça. */
  async function restoreDefaults() {
    setRestoring(true);
    try {
      await putSearchProfile(defaultFilters());
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

  // Polling a cada 10 s só enquanto alguma vaga estiver "pendente"; pausa com a aba oculta,
  // para num erro e ao desmontar.
  const pending = hasPending(items);
  const polling = pending && pollError === null && visible;
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
        const wasScoring = hasPending(itemsRef.current);
        apply({ ...page, items: merged });
        const scoring = hasPending(merged);
        // A fila da IA esvaziou: as notas novas entram nos contadores da casca.
        if (wasScoring && !scoring) refreshOverview();
        if (scoring) timer = setTimeout(poll, POLL_MS);
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

  // Consulta semântica esperando o vetor: a cada 10 s relê o perfil de busca e para quando o
  // SERVIDOR disser que não está mais pendente (query_used pode ficar false para sempre, ex.:
  // peso da consulta 0). Sem perfil vetorizado o worker não ranqueia: não consulta. Pausa com
  // a aba oculta.
  const queryPolling = queryPending && meta !== null && meta.vectorized && pollError === null && visible;
  useEffect(() => {
    if (!queryPolling) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      const seq = saveSeq.current;
      try {
        const profile = await getSearchProfile();
        if (cancelled) return;
        // Um salvamento no meio traz o próprio query_pending: esta resposta não vale.
        if (seq !== saveSeq.current || profile.query_pending) {
          timer = setTimeout(poll, POLL_MS);
          return;
        }
        setQueryPending(false);
        // O vetor ficou pronto: a lista passa a usar a consulta.
        void reloadList();
      } catch (err) {
        if (!cancelled) setPollError(message(err));
      }
    };
    timer = setTimeout(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [queryPolling, reloadList]);

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

  async function prepareSelected() {
    const jobId = selectedRef.current;
    if (jobId === null || preparing) return;
    setPreparing(true);
    setPrepareError(null);
    try {
      const applicationId = await prepareApplication(jobId);
      router.push(`/candidaturas/${applicationId}`);
    } catch (e) {
      setPrepareError(e instanceof ApiError ? e.message : "Não foi possível preparar a candidatura agora.");
    } finally {
      setPreparing(false);
    }
  }
  useEffect(() => {
    prepareRef.current = () => void prepareSelected();
  });

  /** Salva a vaga no pipeline (candidatura em rascunho, sem gerar nada). Idempotente. */
  async function saveSelected() {
    const jobId = selectedRef.current;
    if (jobId === null || savingJob) return;
    setSavingJob(true);
    setSaveNote(null);
    try {
      await createApplication(jobId);
      if (selectedRef.current === jobId) setSaveNote({ ok: true, text: "Vaga salva no pipeline." });
    } catch (e) {
      setSaveNote({ ok: false, text: e instanceof ApiError ? e.message : "Não foi possível salvar a vaga agora." });
    } finally {
      setSavingJob(false);
    }
  }
  useEffect(() => {
    saveRef.current = () => void saveSelected();
  });
  // O aviso vale só para a vaga em que a pessoa salvou.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSaveNote(null);
  }, [selectedId]);

  function retryDetail() {
    setPanel({ name: "loading" });
    setDetailKey((k) => k + 1);
  }

  // Atalhos: j/k navegam; p prepara a candidatura da vaga selecionada; s salva a vaga no pipeline.
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
      if (e.key === "p") {
        if (selectedRef.current === null) return;
        e.preventDefault();
        prepareRef.current();
        return;
      }
      if (e.key === "s") {
        if (selectedRef.current === null) return;
        e.preventDefault();
        saveRef.current();
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
        description="Ordenadas pela nota contra o seu perfil confirmado e, quando houver, pelo seu gosto. A IA avalia as 30 melhores; as outras mostram a pré-seleção vetorial, em cinza."
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
              {hasProfile && (
                <Link className="button button--secondary" href="/jobs/qualidade">
                  Testar qualidade
                </Link>
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
          {hasProfile && <ProfileSwitcher onSwitched={reboot} disabled={saving || editing || applyingProposal} />}
          {hasProfile && (
            <>
              <ChatBar onApply={applyProposal} disabled={applyingProposal} inputRef={chatInputRef} />
              <FilterChips
                filters={filters}
                queryText={queryText}
                queryPending={queryPending && meta.vectorized}
                onRemove={removeFilterChip}
                onEditQuery={editQuery}
                onEmptied={focusChat}
              />
            </>
          )}
          {hasProfile && (
            <FilterBar
              initial={filters}
              revision={revision}
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
                  onPrepare={() => void prepareSelected()}
                  preparing={preparing}
                  prepareError={prepareError}
                  onSave={() => void saveSelected()}
                  saving={savingJob}
                  saveNote={saveNote}
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
