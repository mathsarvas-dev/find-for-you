"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AnswerBox } from "@/components/interview/AnswerBox";
import { ChatThread } from "@/components/interview/ChatThread";
import {
  answerInterview,
  BUSY_STATES,
  finishInterview,
  getInterview,
  resolveSuggestion,
  retryInterview,
  skipQuestion,
  startInterview,
  type Interview,
  type InterviewState,
  type Message,
  type ResolveAction,
  type SuggestionPayload,
} from "@/lib/interview";
import { ApiError, getProfile } from "@/lib/profile";

const POLL_MS = 2000;
/** Depois disso ainda gerando, o worker pode estar parado ou sem chave do Groq. */
const SLOW_MS = 30_000;

type Phase =
  | { name: "loading" }
  | { name: "load-error"; message: string }
  | { name: "no-profile" }
  | { name: "ready"; data: Interview };

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao falar com a API. Tente de novo.";

/** A pergunta em aberto: a última pergunta do assistente, só em awaiting_answer. */
function currentQuestion(d: Interview): Message | null {
  if (d.state !== "awaiting_answer") return null;
  for (let i = d.messages.length - 1; i >= 0; i--) {
    const m = d.messages[i];
    if (m.role === "assistant" && m.kind === "question") return m;
  }
  return null;
}

function busyText(d: Interview): string {
  if (!d.messages.some((m) => m.kind === "question")) return "Preparando a primeira pergunta.";
  const last = [...d.messages].reverse().find((m) => m.role === "user");
  if (last?.kind === "skip") return "Preparando a próxima pergunta.";
  return "Analisando sua resposta e preparando a próxima pergunta.";
}

/** Perfil → abre (ou retoma) a entrevista → estado atual. Nunca rejeita. */
async function openInterview(): Promise<Phase> {
  try {
    if (!(await getProfile())) return { name: "no-profile" };
    try {
      await startInterview();
    } catch (err) {
      // 409: sem perfil confirmado; mesmo estado vazio.
      if (err instanceof ApiError && err.status === 409) return { name: "no-profile" };
      throw err;
    }
    const data = await getInterview();
    if (!data) return { name: "load-error", message: "A entrevista não foi encontrada. Tente de novo." };
    return { name: "ready", data };
  } catch (err) {
    return { name: "load-error", message: message(err) };
  }
}

const DEFAULT_CLOSING =
  "Entrevista encerrada. As sugestões que você aceitou já estão no seu perfil.";

export default function InterviewPage() {
  const [phase, setPhase] = useState<Phase>({ name: "loading" });
  const [pollError, setPollError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const [confirmFinish, setConfirmFinish] = useState(false);
  const [slow, setSlow] = useState(false);
  const mounted = useRef(true);
  const questionRef = useRef<HTMLDivElement>(null);
  /** Última pergunta já exibida: foca só quando aparece uma nova. */
  const shownQuestion = useRef<number | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const setData = useCallback((data: Interview) => setPhase({ name: "ready", data }), []);

  const apply = useCallback((next: Phase) => {
    if (!mounted.current) return;
    if (next.name === "ready") shownQuestion.current = currentQuestion(next.data)?.id ?? null;
    setPhase(next);
  }, []);

  useEffect(() => {
    // Carga inicial: a API só é alcançável do navegador (sem cache no servidor).
    void openInterview().then(apply);
  }, [apply]);

  function reload() {
    setPhase({ name: "loading" });
    setPollError(null);
    void openInterview().then(apply);
  }

  /** Recarrega a entrevista agora (depois de uma ação). */
  const refresh = useCallback(async () => {
    try {
      const data = await getInterview();
      if (!mounted.current) return;
      if (data) setData(data);
      else setPollError("A entrevista não foi encontrada. Recarregue a página.");
    } catch (err) {
      if (mounted.current) setPollError(message(err));
    }
  }, [setData]);

  const data = phase.name === "ready" ? phase.data : null;
  const workerBusy = data !== null && BUSY_STATES.has(data.state);
  const polling = workerBusy && pollError === null;

  // Consulta a cada 2 s enquanto o worker trabalha; para em awaiting_answer,
  // done ou error, e também num erro de rede (até "Tentar de novo").
  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const next = await getInterview();
        if (cancelled) return;
        if (!next) {
          setPollError("A entrevista não foi encontrada. Recarregue a página.");
          return;
        }
        setData(next);
        if (BUSY_STATES.has(next.state)) timer = setTimeout(poll, POLL_MS);
      } catch (err) {
        if (!cancelled) setPollError(message(err));
      }
    };
    timer = setTimeout(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [polling, setData]);

  // 30 s seguidos gerando: explica a causa provável.
  useEffect(() => {
    if (!workerBusy) return;
    const timer = setTimeout(() => setSlow(true), SLOW_MS);
    return () => {
      clearTimeout(timer);
      setSlow(false);
    };
  }, [workerBusy]);

  // Pergunta nova: move o foco para ela (leitor de tela e teclado seguem juntos).
  const question = data ? currentQuestion(data) : null;
  const questionId = question?.id ?? null;
  useEffect(() => {
    if (questionId === null || questionId === shownQuestion.current) return;
    shownQuestion.current = questionId;
    questionRef.current?.focus();
  }, [questionId]);

  // A pergunta em aberto e o encerramento têm painel próprio; o resto é a conversa.
  const threadMessages = data
    ? data.messages.filter(
        (m) => m.id !== questionId && !(data.state === "done" && m.kind === "info"),
      )
    : [];

  /** Ação que muda o estado da sessão; depois recarrega. */
  async function transition(fn: (id: number) => Promise<{ state: InterviewState }>) {
    if (!data || acting) return;
    setActing(true);
    setActionError(null);
    try {
      const { state } = await fn(data.id);
      if (!mounted.current) return;
      setConfirmFinish(false);
      setData({ ...data, state });
      await refresh();
    } catch (err) {
      if (mounted.current) setActionError(message(err));
    } finally {
      if (mounted.current) setActing(false);
    }
  }

  async function answer(text: string) {
    if (!data) return;
    const { state } = await answerInterview(data.id, text); // erro: o AnswerBox mostra
    if (!mounted.current) return;
    setData({ ...data, state });
    await refresh();
  }

  async function skip() {
    if (!data) return;
    const { state } = await skipQuestion(data.id);
    if (!mounted.current) return;
    setData({ ...data, state });
    await refresh();
  }

  async function resolve(id: number, action: ResolveAction, payload?: SuggestionPayload) {
    await resolveSuggestion(id, action, payload); // erro: o cartão mostra (409/422)
    if (mounted.current) await refresh();
  }

  function retryPolling() {
    setPollError(null);
    // Em generating/working o polling recomeça sozinho; fora disso, recarrega já.
    if (!workerBusy) void refresh();
  }

  return (
    <main className="page page--narrow">
      <div className="page-head">
        <h1>Entrevista do perfil</h1>
        <p>
          Perguntas curtas sobre o que falta no seu perfil, uma de cada vez. Cada resposta pode
          virar uma sugestão de alteração, que você aceita, ajusta ou descarta.
        </p>
      </div>

      {phase.name === "loading" && (
        <section className="panel" aria-busy="true" aria-label="Carregando entrevista">
          <div className="panel__body stack">
            <p className="muted">Abrindo a entrevista…</p>
            <span className="skeleton" aria-hidden="true" />
          </div>
        </section>
      )}

      {phase.name === "load-error" && (
        <section className="panel">
          <div className="notice notice--top" role="alert">
            <strong>Não deu para abrir a entrevista.</strong>
            <p>{phase.message}</p>
          </div>
          <div className="panel__foot">
            <span>
              Suba a API com <code>docker compose up -d api</code> e tente de novo.
            </span>
            <button
              type="button"
              className="button"
              onClick={reload}
            >
              Tentar de novo
            </button>
          </div>
        </section>
      )}

      {phase.name === "no-profile" && (
        <section className="panel" aria-labelledby="sem-perfil">
          <div className="panel__head">
            <h2 id="sem-perfil">Confirme seu perfil primeiro.</h2>
          </div>
          <div className="panel__body">
            <p className="muted">
              A entrevista parte do perfil salvo: ela procura o que falta (números nas
              conquistas, datas, resumo) e pergunta só sobre isso. Envie o currículo e confirme a
              revisão para começar.
            </p>
          </div>
          <div className="panel__foot">
            <Link href="/">← Voltar ao início</Link>
            <Link className="button" href="/onboarding">
              Ir para o perfil
            </Link>
          </div>
        </section>
      )}

      {data && (
        <>
          {(threadMessages.length > 0 || data.suggestions.length > 0) && (
            <ChatThread
              messages={threadMessages}
              suggestions={data.suggestions}
              onResolve={resolve}
            />
          )}

          {pollError && (
            <div className="notice notice--flush interview__notice" role="alert">
              <strong>Perdemos contato com a API.</strong>
              <p>{pollError}</p>
              <div className="actions">
                <button type="button" className="button button--secondary" onClick={retryPolling}>
                  Tentar de novo
                </button>
              </div>
            </div>
          )}

          {data.state === "done" ? (
            <DonePanel data={data} />
          ) : (
            <section
              className="panel interview__current"
              aria-labelledby="pergunta-atual"
              aria-busy={workerBusy || undefined}
            >
              <div className="panel__head">
                <h2 id="pergunta-atual">
                  {question ? "Pergunta atual" : "Próxima pergunta"}
                </h2>
                <span className="mono">
                  pergunta{" "}
                  {question
                    ? data.progress.asked
                    : Math.min(data.progress.asked + 1, data.progress.max)}{" "}
                  de ~{data.progress.max}
                </span>
              </div>

              {question && (
                <div className="panel__body stack">
                  <div
                    ref={questionRef}
                    className="interview__question"
                    tabIndex={-1}
                    aria-live="polite"
                  >
                    <p>{question.content}</p>
                  </div>
                  <AnswerBox disabled={acting} onSubmit={answer} onSkip={skip} />
                </div>
              )}

              {workerBusy && (
                <div className="panel__body stack">
                  <p aria-live="polite">{busyText(data)}</p>
                  <div className="interview__skeleton" aria-hidden="true">
                    <span className="skeleton" />
                    <span className="skeleton skeleton--short" />
                  </div>
                  {slow && !pollError && (
                    <div className="notice notice--warn notice--flush" role="status" aria-live="polite">
                      <strong>A pergunta ainda não saiu.</strong>
                      <p>
                        O worker pode não estar rodando ou a chave do Groq pode estar faltando.
                        Veja <code>docker compose logs worker</code> e confirme que GROQ_API_KEY
                        está definida no arquivo .env (depois reinicie o worker).
                      </p>
                    </div>
                  )}
                  <p className="muted">
                    Costuma levar alguns segundos. A página consulta a entrevista a cada 2
                    segundos.
                  </p>
                </div>
              )}

              {data.state === "error" && (
                <div className="notice notice--top" role="alert">
                  <strong>Não consegui gerar a próxima pergunta.</strong>
                  <p>{data.error || "O worker falhou ao preparar a pergunta."}</p>
                </div>
              )}

              {actionError && (
                <p className="field__error interview__action-error" role="alert">
                  {actionError}
                </p>
              )}

              <div className="panel__foot">
                {confirmFinish ? (
                  <div className="interview__confirm">
                    <span>
                      Encerrar agora? As sugestões pendentes continuam aqui para você revisar.
                    </span>
                    <button
                      type="button"
                      className="link-button"
                      disabled={acting}
                      onClick={() => setConfirmFinish(false)}
                    >
                      Cancelar
                    </button>
                    <button
                      type="button"
                      className="link-button link-button--danger"
                      disabled={acting}
                      onClick={() => void transition(finishInterview)}
                    >
                      Sim, encerrar
                    </button>
                  </div>
                ) : (
                  <>
                    <Link href="/onboarding">← Voltar ao perfil</Link>
                    <button
                      type="button"
                      className="link-button"
                      disabled={acting}
                      onClick={() => setConfirmFinish(true)}
                    >
                      Encerrar entrevista
                    </button>
                  </>
                )}
                {data.state === "error" && !confirmFinish && (
                  <button
                    type="button"
                    className="button"
                    disabled={acting}
                    onClick={() => void transition(retryInterview)}
                  >
                    Tentar de novo
                  </button>
                )}
              </div>
            </section>
          )}
        </>
      )}
    </main>
  );
}

function DonePanel({ data }: { data: Interview }) {
  const closing =
    [...data.messages].reverse().find((m) => m.role === "assistant" && m.kind === "info")
      ?.content || DEFAULT_CLOSING;
  const pending = data.suggestions.filter((s) => s.status === "pending").length;
  return (
    <section className="panel" aria-labelledby="encerrada">
      <div className="panel__head">
        <h2 id="encerrada">Entrevista encerrada</h2>
        <span className="mono">
          {data.progress.asked} {data.progress.asked === 1 ? "pergunta" : "perguntas"}
        </span>
      </div>
      <div className="panel__body stack">
        <p>{closing}</p>
        {pending > 0 && (
          <p className="muted">
            {pending === 1
              ? "Ainda há 1 sugestão pendente acima. Aceite, ajuste ou descarte antes de sair."
              : `Ainda há ${pending} sugestões pendentes acima. Aceite, ajuste ou descarte antes de sair.`}
          </p>
        )}
      </div>
      <div className="panel__foot">
        <span>Respostas e sugestões aceitas ficam guardadas no perfil.</span>
        <Link className="button" href="/onboarding">
          Ver perfil
        </Link>
      </div>
    </section>
  );
}
