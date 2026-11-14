"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  CANDIDATES_PAGE_SIZE,
  MAX_TARGET_LEN,
  MAX_TARGETS,
  candidateStateLabel,
  discoveryCounters,
  getDiscovery,
  listCandidates,
  originKindLabel,
  parseTargets,
  putDiscovery,
  type Candidate,
  type DiscoveryState,
} from "@/lib/discovery";
import { ApiError } from "@/lib/profile";

const POLL_MS = 10_000;

type DiscoveryPanelProps = {
  /**
   * Muda quando algo fora do painel pode ter mexido nas empresas automáticas ativas
   * (pausar, reativar). Cada valor novo relê /discovery uma vez.
   */
  refreshKey?: number;
  /** O número de empresas automáticas mudou: a lista de empresas deve ser relida. */
  onCompaniesChanged?: () => void;
  /** Preferências gravadas (interruptor ou empresas-alvo): o resumo da casca pode mudar. */
  onSaved?: () => void;
};

type Phase =
  | { name: "loading" }
  | { name: "load-error"; message: string }
  | { name: "ready"; data: DiscoveryState };

type SaveState =
  | { name: "idle" }
  | { name: "saving" }
  | { name: "saved" }
  | { name: "invalid"; message: string }
  | { name: "error"; message: string };

type Candidates =
  | { name: "loading" }
  | { name: "error"; message: string }
  | {
      name: "ready";
      total: number;
      items: Candidate[];
      more: "idle" | "loading";
      moreError: string | null;
    };

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao falar com a API. Tente de novo.";

const STATE_TONE: Record<string, "on" | "off" | "wait"> = {
  valid: "on",
  pending: "wait",
};

export function DiscoveryPanel({ refreshKey, onCompaniesChanged, onSaved }: DiscoveryPanelProps) {
  const [phase, setPhase] = useState<Phase>({ name: "loading" });
  /** O que o interruptor mostra; difere de `data.enabled` só enquanto o PUT está em voo. */
  const [enabled, setEnabled] = useState(true);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<"toggle" | "save" | null>(null);
  const [save, setSave] = useState<SaveState>({ name: "idle" });
  const [toggleError, setToggleError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState<Candidates>({ name: "loading" });

  const mounted = useRef(true);
  const inFlight = useRef(false);
  /** Cada leitura ou gravação pega um número; só a mais recente pode aplicar a resposta. */
  const seq = useRef(0);
  const listSeq = useRef(0);
  const knownAuto = useRef<number | null>(null);
  const onChangedRef = useRef(onCompaniesChanged);
  const onSavedRef = useRef(onSaved);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  /** Rascunho atual, lido quando a resposta de um Salvar chega. */
  const draftRef = useRef("");
  const lastKey = useRef(refreshKey);

  const switchHintId = useId();
  const fieldId = useId();
  const hintId = useId();
  const errorId = useId();
  const listId = useId();

  useEffect(() => {
    onChangedRef.current = onCompaniesChanged;
    onSavedRef.current = onSaved;
  }, [onCompaniesChanged, onSaved]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  /** `notify`: false quando a releitura veio da própria página (ela já sabe da mudança). */
  const apply = useCallback((next: DiscoveryState, notify = true) => {
    const before = knownAuto.current;
    knownAuto.current = next.auto_companies;
    setEnabled(next.enabled);
    setPhase({ name: "ready", data: next });
    if (notify && before !== null && before !== next.auto_companies) onChangedRef.current?.();
  }, []);

  /** Primeira leitura (ou retentativa). Preenche o campo com a lista salva. Nunca rejeita. */
  const fetchInitial = useCallback(async () => {
    const my = ++seq.current;
    try {
      const next = await getDiscovery();
      if (!mounted.current || my !== seq.current) return;
      changeDraft(next.target_companies.join("\n"));
      apply(next);
    } catch (err) {
      if (mounted.current && my === seq.current) setPhase({ name: "load-error", message: message(err) });
    }
  }, [apply]);

  useEffect(() => {
    // A API só é alcançável do navegador.
    void fetchInitial();
  }, [fetchInitial]);

  function changeDraft(value: string) {
    draftRef.current = value;
    setDraft(value);
  }

  /**
   * Relê /discovery fora do ciclo do polling (Retomar, refreshKey novo). Com uma gravação
   * em voo não faz nada: a resposta dela já traz o estado atual.
   */
  const refetch = useCallback(async (notify = true) => {
    if (inFlight.current) return;
    const my = ++seq.current;
    try {
      const next = await getDiscovery();
      if (!mounted.current || my !== seq.current) return;
      setPollError(null);
      apply(next, notify);
    } catch (err) {
      if (mounted.current && my === seq.current) setPollError(message(err));
    }
  }, [apply]);

  useEffect(() => {
    if (lastKey.current === refreshKey) return;
    lastKey.current = refreshKey;
    // Antes da primeira carga não há o que reler: a carga inicial já vem fresca.
    if (knownAuto.current === null) return;
    void refetch(false);
  }, [refreshKey, refetch]);

  function reload() {
    setPhase({ name: "loading" });
    void fetchInitial();
  }

  const data = phase.name === "ready" ? phase.data : null;
  const atCap = data !== null && data.auto_companies >= data.max_auto;
  // Só vale consultar se a fila pode andar: ligada, com pendentes e abaixo do teto.
  const polling =
    data !== null && data.enabled && data.pending > 0 && !atCap && pollError === null;

  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      // Gravação em voo: a resposta dela manda; tenta de novo no próximo ciclo.
      if (inFlight.current) {
        timer = setTimeout(poll, POLL_MS);
        return;
      }
      const my = ++seq.current;
      try {
        const next = await getDiscovery();
        if (cancelled) return;
        if (my === seq.current) apply(next);
        timer = setTimeout(poll, POLL_MS);
      } catch (err) {
        if (cancelled) return;
        // Erro de uma consulta já superada por uma gravação: segue o ciclo em vez de parar.
        if (my === seq.current) setPollError(message(err));
        else timer = setTimeout(poll, POLL_MS);
      }
    };
    timer = setTimeout(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [polling, apply]);

  async function toggleEnabled() {
    if (inFlight.current || !data) return;
    const previous = enabled;
    const next = !previous;
    inFlight.current = true;
    const my = ++seq.current;
    setBusy("toggle");
    setEnabled(next);
    setToggleError(null);
    try {
      // Manda a lista já salva, não o rascunho do campo.
      const res = await putDiscovery({ enabled: next, target_companies: data.target_companies });
      onSavedRef.current?.();
      if (mounted.current && my === seq.current) apply(res);
    } catch (err) {
      if (mounted.current) {
        setEnabled(previous);
        setToggleError(message(err));
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(null);
    }
  }

  async function saveTargets() {
    if (inFlight.current || !data) return;
    const parsed = parseTargets(draft);
    if (!parsed.ok) {
      setSave({ name: "invalid", message: parsed.error });
      textareaRef.current?.focus();
      return;
    }
    inFlight.current = true;
    const my = ++seq.current;
    const sent = draft;
    setBusy("save");
    setSave({ name: "saving" });
    try {
      const res = await putDiscovery({ enabled: data.enabled, target_companies: parsed.names });
      onSavedRef.current?.();
      if (!mounted.current) return;
      if (my === seq.current) apply(res);
      // Só troca o campo pela lista normalizada se ninguém digitou enquanto salvava.
      if (draftRef.current === sent) {
        changeDraft(res.target_companies.join("\n"));
        setSave({ name: "saved" });
      } else {
        setSave({ name: "idle" });
      }
    } catch (err) {
      if (mounted.current) setSave({ name: "error", message: message(err) });
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(null);
    }
  }

  async function fetchCandidates() {
    const my = ++listSeq.current;
    setCandidates({ name: "loading" });
    try {
      const page = await listCandidates({ limit: CANDIDATES_PAGE_SIZE, offset: 0 });
      if (!mounted.current || my !== listSeq.current) return;
      setCandidates({ name: "ready", total: page.total, items: page.items, more: "idle", moreError: null });
    } catch (err) {
      if (mounted.current && my === listSeq.current) setCandidates({ name: "error", message: message(err) });
    }
  }

  async function loadMore() {
    if (candidates.name !== "ready" || candidates.more === "loading") return;
    const my = listSeq.current;
    const offset = candidates.items.length;
    setCandidates({ ...candidates, more: "loading", moreError: null });
    try {
      const page = await listCandidates({ limit: CANDIDATES_PAGE_SIZE, offset });
      if (!mounted.current || my !== listSeq.current) return;
      setCandidates((c) =>
        c.name === "ready"
          ? { name: "ready", total: page.total, items: [...c.items, ...page.items], more: "idle", moreError: null }
          : c,
      );
    } catch (err) {
      if (!mounted.current || my !== listSeq.current) return;
      setCandidates((c) => (c.name === "ready" ? { ...c, more: "idle", moreError: message(err) } : c));
    }
  }

  function toggleCandidates() {
    if (open) {
      // Descarta respostas em voo da lista fechada.
      listSeq.current++;
      setOpen(false);
      return;
    }
    setOpen(true);
    void fetchCandidates();
  }

  const fieldError = save.name === "invalid" || save.name === "error" ? save.message : null;

  return (
    <section className="panel discovery" aria-labelledby="descoberta">
      <div className="panel__head">
        <h2 id="descoberta">Descoberta automática</h2>
        {data && <span className="mono">{discoveryCounters(data)}</span>}
      </div>

      {phase.name === "loading" && (
        <div className="panel__body stack" aria-busy="true" aria-label="Carregando a descoberta automática">
          <p className="muted">Buscando as preferências de descoberta…</p>
          <span className="skeleton" aria-hidden="true" />
        </div>
      )}

      {phase.name === "load-error" && (
        <>
          <div className="notice notice--top" role="alert">
            <strong>Não deu para carregar a descoberta automática.</strong>
            <p>{phase.message}</p>
          </div>
          <div className="panel__foot">
            <span>As empresas cadastradas continuam sendo coletadas.</span>
            <button type="button" className="button" onClick={reload}>
              Tentar de novo
            </button>
          </div>
        </>
      )}

      {data && (
        <>
          <div className="panel__body discovery__body">
            <div className="discovery__switch">
              <label className="switch">
                <input
                  type="checkbox"
                  role="switch"
                  checked={enabled}
                  disabled={busy !== null}
                  aria-busy={busy === "toggle" || undefined}
                  aria-describedby={switchHintId}
                  onChange={() => void toggleEnabled()}
                />
                <span className="switch__track" aria-hidden="true" />
                <span className="switch__label">Descobrir empresas automaticamente</span>
              </label>
              <p className="field__hint" id={switchHintId}>
                {enabled
                  ? "Procura quadros no Greenhouse a partir do seu currículo, de um catálogo e da lista abaixo. Os que têm vagas entram em Empresas vigiadas."
                  : "Desligada: nada novo é procurado. As empresas automáticas já adicionadas continuam vigiadas."}{" "}
                <span className="mono">
                  {`${data.auto_companies} de ${data.max_auto} automáticas`}
                </span>
              </p>
              {toggleError && (
                <p className="field__error" role="alert">
                  {toggleError}
                </p>
              )}
              {atCap && data.enabled && data.pending > 0 && (
                <p className="discovery__cap">
                  Limite de {data.max_auto} empresas automáticas atingido; os candidatos na fila
                  esperam até você pausar alguma (pausar libera uma vaga).
                </p>
              )}
            </div>

            <form
              className="discovery__targets"
              noValidate
              onSubmit={(e) => {
                e.preventDefault();
                void saveTargets();
              }}
            >
              <div className="field">
                <label className="field__label" htmlFor={fieldId}>
                  Empresas que você quer acompanhar
                </label>
                <textarea
                  ref={textareaRef}
                  id={fieldId}
                  className="input discovery__textarea"
                  rows={5}
                  spellCheck={false}
                  autoComplete="off"
                  placeholder={"Nubank\nGitLab"}
                  value={draft}
                  aria-describedby={fieldError ? `${hintId} ${errorId}` : hintId}
                  aria-invalid={save.name === "invalid" || undefined}
                  onChange={(e) => {
                    changeDraft(e.target.value);
                    if (save.name !== "saving") setSave({ name: "idle" });
                  }}
                />
                <p className="field__hint" id={hintId}>
                  Uma por linha, até {MAX_TARGETS} nomes de {MAX_TARGET_LEN} caracteres. Repetidos
                  são ignorados.
                </p>
              </div>
              {fieldError && (
                <p className="field__error discovery__msg" id={errorId} role="alert">
                  {fieldError}
                </p>
              )}
              <div className="discovery__foot">
                <p className="muted discovery__status" role="status" aria-live="polite">
                  {save.name === "saving"
                    ? "Salvando a lista…"
                    : save.name === "saved"
                      ? "Lista salva."
                      : ""}
                </p>
                <button
                  type="submit"
                  className="button"
                  disabled={busy !== null}
                  aria-busy={busy === "save" || undefined}
                >
                  {busy === "save" ? "Salvando…" : "Salvar"}
                </button>
              </div>
            </form>
          </div>

          {pollError && (
            <div className="notice sources__notice" role="alert">
              <strong>Parei de acompanhar a fila.</strong>
              <p>{pollError}</p>
              <div className="actions">
                <button
                  type="button"
                  className="button button--secondary"
                  disabled={busy !== null}
                  onClick={() => void refetch()}
                >
                  Retomar
                </button>
              </div>
            </div>
          )}

          <div className="discovery__candidates">
            <button
              type="button"
              className="link-button"
              aria-expanded={open}
              aria-controls={listId}
              onClick={toggleCandidates}
            >
              {open ? "Esconder candidatos" : "Ver candidatos"}
            </button>
            {polling && (
              <span className="muted discovery__polling">
                A fila anda sozinha; a página confere a cada 10 segundos.
              </span>
            )}
          </div>

          {open && (
            <div id={listId} className="discovery__list">
              {candidates.name === "loading" && (
                <p className="muted discovery__list-msg" aria-busy="true">
                  Buscando candidatos…
                </p>
              )}
              {candidates.name === "error" && (
                <div className="notice sources__notice" role="alert">
                  <strong>Não deu para listar os candidatos.</strong>
                  <p>{candidates.message}</p>
                  <div className="actions">
                    <button
                      type="button"
                      className="button button--secondary"
                      onClick={() => void fetchCandidates()}
                    >
                      Tentar de novo
                    </button>
                  </div>
                </div>
              )}
              {candidates.name === "ready" &&
                (candidates.items.length === 0 ? (
                  <p className="muted discovery__list-msg">
                    Nenhum candidato ainda. A primeira busca sai do seu currículo e do catálogo
                    alguns minutos depois de ligar a descoberta.
                  </p>
                ) : (
                  <>
                    <ul className="candidate-list" aria-label="Candidatos">
                      {candidates.items.map((c, i) => (
                        <CandidateRow key={`${c.token}-${i}`} candidate={c} />
                      ))}
                    </ul>
                    <div className="discovery__list-foot">
                      <span className="mono" aria-live="polite">
                        {`${candidates.items.length} de ${candidates.total}`}
                      </span>
                      {candidates.moreError && (
                        <span className="field__error" role="alert">
                          {candidates.moreError}
                        </span>
                      )}
                      {candidates.items.length < candidates.total && (
                        <button
                          type="button"
                          className="button button--secondary"
                          disabled={candidates.more === "loading"}
                          aria-busy={candidates.more === "loading" || undefined}
                          onClick={() => void loadMore()}
                        >
                          {candidates.more === "loading" ? "Carregando…" : "Carregar mais"}
                        </button>
                      )}
                    </div>
                  </>
                ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}

const LAST_ERROR_LABELS: Record<string, string> = {
  SourceNotFound: "quadro não encontrado",
  SourceUnavailable: "fonte indisponível",
  TimeoutError: "fonte indisponível",
  SourceBadResponse: "resposta inesperada",
};

function lastErrorLabel(raw: string): string {
  return Object.prototype.hasOwnProperty.call(LAST_ERROR_LABELS, raw)
    ? LAST_ERROR_LABELS[raw]
    : "erro na validação";
}

function CandidateRow({ candidate: c }: { candidate: Candidate }) {
  const tone = STATE_TONE[c.state] ?? "off";
  return (
    <li className="candidate">
      <p className="candidate__title">
        {c.name_hint && <span className="candidate__name">{c.name_hint}</span>}
        <span className="mono candidate__token">{c.token}</span>
      </p>
      <p className="candidate__meta">
        <span className="chip" data-state={tone}>
          {candidateStateLabel(c.state)}
        </span>
        <span>{`origem: ${originKindLabel(c.origin_kind)}`}</span>
      </p>
      {c.last_error && <p className="candidate__error">{lastErrorLabel(c.last_error)}</p>}
    </li>
  );
}
