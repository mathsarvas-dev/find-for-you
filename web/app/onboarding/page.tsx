"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useState } from "react";
import { onboardingSteps, type OnboardingStage } from "@/components/onboarding/onboardingSteps";
import { ReviewStep } from "@/components/onboarding/ReviewStep";
import { UploadStep } from "@/components/onboarding/UploadStep";
import { VectorStatus } from "@/components/onboarding/VectorStatus";
import { useRefreshOverview } from "@/components/shell/OverviewContext";
import { PageHeader } from "@/components/shell/PageHeader";
import { Stepper } from "@/components/stepper/Stepper";
import {
  ApiError,
  confirmProfile,
  continueWithoutExtraction,
  getImport,
  getProfile,
  retryCountdown,
  uploadResume,
  type CanonicalProfile,
  type ImportStatus,
  type ProfileState,
} from "@/lib/profile";

const POLL_MS = 2000;
/** Depois disso sem terminar a vetorização, explica a demora. */
const SLOW_MS = 30_000;
/** Depois disso ainda "na fila", o worker pode estar parado ou sem chave do Groq. */
const PENDING_SLOW_MS = 30_000;

type Phase =
  | { name: "loading" }
  | { name: "load-error"; message: string }
  | { name: "existing"; current: ProfileState }
  | { name: "upload" }
  | { name: "reading"; importId: number; startedAt: number }
  | { name: "read-error"; importId: number; message: string; kind: "import" | "network" }
  | { name: "review"; profile: CanonicalProfile; truncated: boolean }
  | { name: "vectors"; total: number; embedded: number; startedAt: number; error: string | null };

/** Estágio da trilha do cabeçalho; null enquanto não se sabe (carregando, erro ao carregar). */
function stageOf(phase: Phase): OnboardingStage | null {
  switch (phase.name) {
    case "upload":
      return "upload";
    case "reading":
    case "read-error":
      return "reading";
    case "review":
      return "review";
    case "vectors":
      return phase.embedded < phase.total ? "vectorizing" : "ready";
    case "existing":
      return phase.current.chunks_embedded < phase.current.chunks_total ? "vectorizing" : "ready";
    default:
      return null;
  }
}

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao falar com a API. Tente de novo.";

function elapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export default function OnboardingPage() {
  const [phase, setPhase] = useState<Phase>({ name: "loading" });
  const [hasProfile, setHasProfile] = useState(false);
  const [narrative, setNarrative] = useState("");
  const [lastFile, setLastFile] = useState<File | null>(null);
  const [importStatus, setImportStatus] = useState<ImportStatus>("pending");
  const [retryAt, setRetryAt] = useState<string | null>(null);
  const [skipError, setSkipError] = useState<string | null>(null);
  // Só lido em handlers e efeitos: Date.now() no render quebra o prerender.
  const [now, setNow] = useState(0);
  const narrativeId = useId();
  const narrativeHintId = useId();
  const refreshOverview = useRefreshOverview();

  const load = useCallback(async () => {
    setPhase({ name: "loading" });
    try {
      const current = await getProfile();
      if (current) {
        setHasProfile(true);
        setNarrative(current.narrative ?? "");
        setPhase({ name: "existing", current });
      } else {
        setPhase({ name: "upload" });
      }
    } catch (err) {
      setPhase({ name: "load-error", message: message(err) });
    }
  }, []);

  useEffect(() => {
    // Carga inicial: a API só é alcançável do navegador (sem cache no servidor).
    void load();
  }, [load]);

  const startReading = useCallback((importId: number) => {
    setImportStatus("pending");
    setRetryAt(null);
    setSkipError(null);
    setNow(Date.now());
    setPhase({ name: "reading", importId, startedAt: Date.now() });
  }, []);

  // Relógio para o tempo decorrido (leitura e vetorização).
  const ticking = phase.name === "reading" || phase.name === "vectors";
  useEffect(() => {
    if (!ticking) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ticking]);

  // Passo 2: consulta a importação a cada 2 s até ficar pronta ou falhar.
  const readingId = phase.name === "reading" ? phase.importId : null;
  useEffect(() => {
    if (readingId === null) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const st = await getImport(readingId);
        if (cancelled) return;
        if (st.status === "ready" && st.profile) {
          setPhase({ name: "review", profile: st.profile, truncated: st.truncated });
        } else if (st.status === "error") {
          setPhase({
            name: "read-error",
            importId: readingId,
            kind: "import",
            message: st.error || "A leitura do currículo falhou.",
          });
        } else {
          setImportStatus(st.status);
          setRetryAt(st.retry_at ?? null);
          timer = setTimeout(poll, POLL_MS);
        }
      } catch (err) {
        if (!cancelled) {
          setPhase({ name: "read-error", importId: readingId, kind: "network", message: message(err) });
        }
      }
    };
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [readingId]);

  // Depois de confirmar: consulta GET /profile até vetorizar todos os trechos.
  const vectorsPending =
    phase.name === "vectors" && phase.error === null && phase.embedded < phase.total;
  useEffect(() => {
    if (!vectorsPending) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const st = await getProfile();
        if (cancelled || !st) return;
        // Vetores prontos: a casca passa a mostrar o perfil vetorizado.
        if (st.chunks_embedded >= st.chunks_total) refreshOverview();
        setPhase((p) =>
          p.name === "vectors" ? { ...p, total: st.chunks_total, embedded: st.chunks_embedded } : p,
        );
      } catch (err) {
        if (!cancelled) setPhase((p) => (p.name === "vectors" ? { ...p, error: message(err) } : p));
      }
    }, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // Reagenda a cada resposta nova (embedded muda) ou ao retomar após erro.
  }, [vectorsPending, phase, refreshOverview]);

  async function retryRead() {
    if (phase.name !== "read-error") return;
    if (phase.kind === "network") {
      startReading(phase.importId);
      return;
    }
    if (!lastFile) {
      setPhase({ name: "upload" });
      return;
    }
    try {
      const { id } = await uploadResume(lastFile);
      startReading(id);
    } catch (err) {
      setPhase({ ...phase, message: message(err) });
    }
  }

  async function skipExtraction(importId: number) {
    setSkipError(null);
    try {
      await continueWithoutExtraction(importId);
      refreshOverview();
      await load();
    } catch (err) {
      setSkipError(message(err));
    }
  }

  async function reviewExtracted(importId: number) {
    try {
      const st = await getImport(importId);
      if (st.status === "ready" && st.profile) {
        setPhase({ name: "review", profile: st.profile, truncated: st.truncated });
      }
    } catch (err) {
      setPhase({ name: "load-error", message: message(err) });
    }
  }

  async function confirm(profile: CanonicalProfile, text: string) {
    setNarrative(text);
    const result = await confirmProfile(profile, text);
    refreshOverview();
    setNow(Date.now());
    setHasProfile(true);
    setPhase({
      name: "vectors",
      total: result.chunks_total,
      embedded: result.chunks_embedded,
      startedAt: Date.now(),
      error: null,
    });
  }

  const stage = stageOf(phase);
  const startNew = () => setPhase({ name: "upload" });

  return (
    <main className="page page--narrow">
      <PageHeader
        title="Seu perfil"
        description="O Rumo lê seu currículo, você confere o que ele entendeu e o perfil passa a ser a base para comparar vagas."
        actions={
          phase.name === "existing" ? (
            <>
              <Link className="button" href="/onboarding/interview">
                Continuar entrevista
              </Link>
              <button type="button" className="button button--secondary" onClick={startNew}>
                Enviar currículo
              </button>
            </>
          ) : hasProfile && phase.name !== "loading" ? (
            <Link
              className={stage === "ready" ? "button" : "button button--secondary"}
              href="/onboarding/interview"
            >
              Continuar entrevista
            </Link>
          ) : undefined
        }
        stepper={
          stage !== null && (
            <Stepper steps={onboardingSteps(stage)} orientation="horizontal" label="Etapas do perfil" />
          )
        }
      />

      {phase.name === "loading" && (
        <section className="panel" aria-busy="true" aria-label="Carregando perfil">
          <div className="panel__body">
            <p className="muted">Verificando se você já tem um perfil salvo…</p>
            <span className="skeleton" aria-hidden="true" />
          </div>
        </section>
      )}

      {phase.name === "load-error" && (
        <section className="panel">
          <div className="notice notice--top" role="alert">
            <strong>Não deu para carregar seu perfil.</strong>
            <p>{phase.message}</p>
          </div>
          <div className="panel__foot">
            <span>
              Suba a API com <code>docker compose up -d api</code> e tente de novo.
            </span>
            <button type="button" className="button" onClick={() => void load()}>
              Tentar de novo
            </button>
          </div>
        </section>
      )}

      {phase.name === "existing" && (
        <ExistingProfile
          current={phase.current}
          now={now}
          onReviewExtracted={(id) => void reviewExtracted(id)}
          onView={() => setPhase({ name: "review", profile: phase.current.profile, truncated: false })}
          onNew={startNew}
        />
      )}

      {phase.name === "upload" && (
        <section className="panel" aria-labelledby="passo-1">
          <div className="panel__head">
            <h2 id="passo-1">Sobre você</h2>
          </div>
          <div className="panel__body stack">
            <div className="field">
              <label className="field__label" htmlFor={narrativeId}>
                Conte com suas palavras o que você procura (opcional)
              </label>
              <p className="field__hint" id={narrativeHintId}>
                Tipo de vaga, área, modelo de trabalho, o que não quer mais. Você pode editar
                isso na revisão.
              </p>
              <textarea
                id={narrativeId}
                className="input"
                rows={4}
                aria-describedby={narrativeHintId}
                placeholder="Ex.: Quero continuar com análise de dados, de preferência remoto, em empresa de logística ou varejo."
                value={narrative}
                onChange={(e) => setNarrative(e.target.value)}
              />
            </div>
            <UploadStep
              onUploaded={({ id }, file) => {
                setLastFile(file);
                startReading(id);
              }}
            />
          </div>
          <div className="panel__foot">
            {hasProfile ? (
              <button type="button" className="link-button" onClick={() => void load()}>
                ← Voltar ao perfil atual
              </button>
            ) : (
              <Link href="/">← Voltar ao início</Link>
            )}
          </div>
        </section>
      )}

      {phase.name === "reading" && (
        <section className="panel" aria-labelledby="passo-2" aria-busy="true">
          <div className="panel__head">
            <h2 id="passo-2">Lendo seu currículo</h2>
            <span className="mono">{elapsed(now - phase.startedAt)}</span>
          </div>
          <div className="panel__body stack">
            <p aria-live="polite">
              {importStatus === "extracting"
                ? "Extraindo os dados: cargos, datas, conquistas e habilidades."
                : retryAt
                  ? `O limite do serviço de leitura foi atingido; ${retryCountdown(retryAt, now)}.`
                  : "Na fila para leitura. Começa em instantes."}
            </p>
            {skipError && (
              <div className="notice" role="alert">
                <p>{skipError}</p>
              </div>
            )}
            {importStatus === "pending" && now - phase.startedAt >= PENDING_SLOW_MS && (
              <div className="notice notice--warn notice--flush" role="status" aria-live="polite">
                <strong>A leitura não começou.</strong>
                <p>
                  O worker pode não estar rodando ou a chave do Groq pode estar faltando. Veja{" "}
                  <code>docker compose logs worker</code> e confirme que GROQ_API_KEY está definida
                  no arquivo .env (depois reinicie o worker).
                </p>
              </div>
            )}
            <p className="muted">
              Costuma levar menos de um minuto. A página consulta a importação a cada 2 segundos.
            </p>
          </div>
          <div className="panel__foot">
            <button type="button" className="link-button" onClick={() => setPhase({ name: "upload" })}>
              ← Voltar e escolher outro arquivo
            </button>
            <button
              type="button"
              className="button button--secondary"
              onClick={() => void skipExtraction(phase.importId)}
            >
              Continuar sem extrair
            </button>
            <span className="mono">importação #{phase.importId}</span>
          </div>
        </section>
      )}

      {phase.name === "read-error" && (
        <section className="panel" aria-labelledby="passo-2">
          <div className="panel__head">
            <h2 id="passo-2">Leitura do currículo</h2>
            <span className="mono">importação #{phase.importId}</span>
          </div>
          <div className="notice notice--top" role="alert">
            <strong>
              {phase.kind === "network" ? "Perdemos contato com a API." : "Não consegui ler o currículo."}
            </strong>
            <p>{phase.message}</p>
          </div>
          <div className="panel__foot">
            <button type="button" className="link-button" onClick={() => setPhase({ name: "upload" })}>
              ← Escolher outro arquivo
            </button>
            {phase.kind === "import" && (
              <button
                type="button"
                className="button button--secondary"
                onClick={() => void skipExtraction(phase.importId)}
              >
                Continuar sem extrair
              </button>
            )}
            <button type="button" className="button" onClick={() => void retryRead()}>
              Tentar de novo
            </button>
          </div>
        </section>
      )}

      {phase.name === "review" && (
        <>
          {phase.truncated && (
            <div className="notice notice--warn notice--flush" role="status">
              <strong>O currículo era longo e só o começo foi lido.</strong>
              <p>Confira se faltou alguma experiência no fim e adicione abaixo.</p>
            </div>
          )}
          <p className="review__intro">
            Confira o que foi lido. Corrija o que estiver errado, remova o que não quer usar e
            adicione o que faltou. Nada é salvo antes de confirmar.
          </p>
          <ReviewStep
            profile={phase.profile}
            narrative={narrative}
            onConfirm={confirm}
            onBack={() => setPhase({ name: "upload" })}
          />
        </>
      )}

      {phase.name === "vectors" && (
        <section className="panel" aria-labelledby="salvo">
          <div className="panel__head">
            <h2 id="salvo">Perfil salvo</h2>
            <span className="mono">{elapsed(now - phase.startedAt)}</span>
          </div>
          <div className="panel__body">
            <VectorStatus
              total={phase.total}
              embedded={phase.embedded}
              slow={now - phase.startedAt >= SLOW_MS}
            />
          </div>
          {phase.error && (
            <div className="notice" role="alert">
              <strong>Não consegui consultar o progresso.</strong>
              <p>{phase.error}</p>
            </div>
          )}
          <div className="panel__foot">
            <Link href="/">← Voltar ao início</Link>
            {phase.error ? (
              <button
                type="button"
                className="button"
                onClick={() => setPhase({ ...phase, error: null })}
              >
                Tentar de novo
              </button>
            ) : (
              <Link className="button" href="/onboarding/interview">
                Continuar para a entrevista
              </Link>
            )}
          </div>
        </section>
      )}
    </main>
  );
}

function ExistingProfile({
  current,
  now,
  onReviewExtracted,
  onView,
  onNew,
}: {
  current: ProfileState;
  now: number;
  onReviewExtracted: (importId: number) => void;
  onView: () => void;
  onNew: () => void;
}) {
  const { basics, experiences, skills } = current.profile;
  return (
    <section className="panel" aria-labelledby="perfil-atual">
      <div className="panel__head">
        <h2 id="perfil-atual">Perfil atual</h2>
        <span className="mono">versão {current.version}</span>
      </div>
      <div className="panel__body stack">
        {current.provisional && <ProvisionalNotice current={current} now={now} onReview={onReviewExtracted} />}
        <div>
          <p className="existing__name">{basics.name || "Sem nome"}</p>
          {basics.headline && <p className="muted">{basics.headline}</p>}
        </div>
        <dl className="facts">
          <div>
            <dt>Experiências</dt>
            <dd className="mono">{experiences.length}</dd>
          </div>
          <div>
            <dt>Habilidades</dt>
            <dd className="mono">{skills.length}</dd>
          </div>
          <div>
            <dt>Trechos vetorizados</dt>
            <dd className="mono">
              {current.chunks_embedded} de {current.chunks_total}
            </dd>
          </div>
        </dl>
        <p className="muted">
          Você pode revisar e editar o perfil salvo, ou enviar outro currículo para começar de uma
          leitura nova. Confirmar cria uma nova versão; a anterior fica guardada.
        </p>
        <p>
          <Link href="/onboarding/interview">Continuar para a entrevista</Link>
          <span className="muted"> · perguntas curtas para completar o que falta no perfil.</span>
        </p>
      </div>
      <div className="panel__foot">
        <button type="button" className="button button--secondary" onClick={onNew}>
          Enviar outro currículo
        </button>
        <button type="button" className="button" onClick={onView}>
          Ver perfil atual
        </button>
      </div>
    </section>
  );
}

function ProvisionalNotice({
  current,
  now,
  onReview,
}: {
  current: ProfileState;
  now: number;
  onReview: (importId: number) => void;
}) {
  const imp = current.import;
  if (imp?.status === "ready") {
    return (
      <div className="notice notice--warn notice--flush" role="status">
        <strong>Dados extraídos — revise.</strong>
        <p>Confirmar troca o perfil provisório pelo perfil estruturado.</p>
        <button type="button" className="button" onClick={() => onReview(imp.id)}>
          Revisar dados extraídos
        </button>
      </div>
    );
  }
  const countdown = imp?.status === "pending" ? retryCountdown(imp.retry_at, now) : null;
  return (
    <div className="notice notice--warn notice--flush" role="status">
      <strong>Perfil provisório (sem extração).</strong>
      <p>
        A coleta e o ranking já usam o texto do currículo; candidaturas só depois da revisão.{" "}
        {imp?.status === "error"
          ? "A extração falhou; envie o currículo de novo."
          : countdown
            ? `A extração segue na fila: ${countdown}.`
            : "A extração segue na fila."}
      </p>
    </div>
  );
}
