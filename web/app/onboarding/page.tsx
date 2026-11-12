"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useState } from "react";
import { ReviewStep } from "@/components/onboarding/ReviewStep";
import { UploadStep } from "@/components/onboarding/UploadStep";
import { VectorStatus } from "@/components/onboarding/VectorStatus";
import {
  ApiError,
  confirmProfile,
  getImport,
  getProfile,
  uploadResume,
  type CanonicalProfile,
  type ImportStatus,
  type ProfileState,
} from "@/lib/profile";

const POLL_MS = 2000;
/** Depois disso sem terminar a vetorização, explica a demora. */
const SLOW_MS = 30_000;

type Phase =
  | { name: "loading" }
  | { name: "load-error"; message: string }
  | { name: "existing"; current: ProfileState }
  | { name: "upload" }
  | { name: "reading"; importId: number; startedAt: number }
  | { name: "read-error"; importId: number; message: string; kind: "import" | "network" }
  | { name: "review"; profile: CanonicalProfile; truncated: boolean }
  | { name: "vectors"; total: number; embedded: number; startedAt: number; error: string | null };

const STEPS = ["Sobre você", "Leitura do currículo", "Revisão"] as const;

function stepOf(phase: Phase): number | null {
  switch (phase.name) {
    case "upload":
      return 0;
    case "reading":
    case "read-error":
      return 1;
    case "review":
    case "vectors":
      return 2;
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
  // Só lido em handlers e efeitos: Date.now() no render quebra o prerender.
  const [now, setNow] = useState(0);
  const narrativeId = useId();
  const narrativeHintId = useId();

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
  }, [vectorsPending, phase]);

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

  async function confirm(profile: CanonicalProfile, text: string) {
    setNarrative(text);
    const result = await confirmProfile(profile, text);
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

  const step = stepOf(phase);

  return (
    <main className="page page--narrow">
      <div className="page-head">
        <h1>Seu perfil</h1>
        <p>
          O Rumo lê seu currículo, você confere o que ele entendeu e o perfil passa a ser a base
          para comparar vagas.
        </p>
      </div>

      {step !== null && <Steps current={step} />}

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
          onView={() => setPhase({ name: "review", profile: phase.current.profile, truncated: false })}
          onNew={() => setPhase({ name: "upload" })}
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
                : "Na fila para leitura. Começa em instantes."}
            </p>
            <p className="muted">
              Costuma levar menos de um minuto. A página consulta a importação a cada 2 segundos.
            </p>
          </div>
          <div className="panel__foot">
            <button type="button" className="link-button" onClick={() => setPhase({ name: "upload" })}>
              ← Voltar e escolher outro arquivo
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
            {phase.error && (
              <button
                type="button"
                className="button"
                onClick={() => setPhase({ ...phase, error: null })}
              >
                Tentar de novo
              </button>
            )}
          </div>
        </section>
      )}
    </main>
  );
}

function Steps({ current }: { current: number }) {
  return (
    <nav className="steps" aria-label="Etapas">
      <p className="steps__count mono">
        Passo {current + 1} de {STEPS.length}
      </p>
      <ol className="steps__list">
        {STEPS.map((label, i) => (
          <li
            key={label}
            className="steps__item"
            data-state={i < current ? "done" : i === current ? "current" : "todo"}
            aria-current={i === current ? "step" : undefined}
          >
            <span className="mono steps__num" aria-hidden="true">
              {i + 1}
            </span>
            {label}
          </li>
        ))}
      </ol>
    </nav>
  );
}

function ExistingProfile({
  current,
  onView,
  onNew,
}: {
  current: ProfileState;
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
