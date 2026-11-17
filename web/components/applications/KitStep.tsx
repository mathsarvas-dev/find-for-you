"use client";

import { useState } from "react";
import { useOverview, useRefreshOverview } from "@/components/shell/OverviewContext";
import {
  applyApplication,
  artifactUrl,
  downloadArtifact,
  unapplyApplication,
  type ApplicationDetail,
} from "@/lib/applications";
import { ApiError } from "@/lib/profile";
import { safeJobUrl } from "@/lib/sources";

type Props = {
  detail: ApplicationDetail;
  /** Chamado depois de aplicar/desfazer: a página recarrega a candidatura. */
  onChanged: () => void;
};

const PROVISIONAL_REASON =
  "Seu perfil veio de um texto colado, sem extração. Confirme o perfil antes de marcar como aplicada.";

async function copyTextOf(url: string): Promise<void> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, `A API respondeu com erro (HTTP ${res.status}).`);
  await navigator.clipboard.writeText(await res.text());
}

function dateText(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("pt-BR");
}

/** Passo "Kit": PDF e texto, respostas finais copiáveis, a vaga e o "Apliquei" (sem envio). */
export function KitStep({ detail, onChanged }: Props) {
  const refresh = useRefreshOverview();
  const { overview } = useOverview();
  const provisional = overview?.profile.provisional === true;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  // the last action on this kit wins; before any action the detail says whether it is applied
  const [local, setLocal] = useState<{ applied: boolean; at: string | null } | null>(null);

  const resumeReady = detail.resume?.status === "valid";
  const letterReady = detail.cover_letter?.status === "valid";
  const finals = detail.questions.filter((q) => q.answer_state === "final" && q.answer);
  const jobHref = safeJobUrl(detail.job.url);
  const isApplied = local ? local.applied : detail.state === "applied";
  const appliedAt = local ? local.at : detail.applied_at;
  const canApply = detail.state === "ready" && !provisional;

  async function run(action: () => Promise<void>, done: string) {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      await action();
      setStatus(done);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Algo deu errado. Tente de novo.");
    } finally {
      setBusy(false);
    }
  }

  async function markApplied() {
    await run(async () => {
      const app = await applyApplication(detail.id);
      setLocal({ applied: true, at: app.applied_at });
      refresh();
      onChanged();
    }, "Candidatura marcada como aplicada.");
  }

  async function undoApplied() {
    await run(async () => {
      await unapplyApplication(detail.id);
      setLocal({ applied: false, at: null });
      refresh();
      onChanged();
    }, "Marcação desfeita.");
  }

  return (
    <section className="application-step" aria-labelledby="kit-title">
      <h2 id="kit-title" className="application-step__title">
        Kit para a candidatura
      </h2>

      <div className="application-downloads">
        <button
          type="button"
          className="button"
          disabled={!resumeReady || busy}
          onClick={() => run(() => downloadArtifact(detail.id, "resume", "pdf"), "PDF baixado.")}
        >
          Baixar PDF
        </button>
        <button
          type="button"
          className="button button--secondary"
          disabled={!resumeReady || busy}
          onClick={() =>
            run(() => copyTextOf(artifactUrl(detail.id, "resume", "txt")), "Texto copiado.")
          }
        >
          Copiar texto
        </button>
        {detail.cover_letter ? (
          <>
            <button
              type="button"
              className="button button--secondary"
              disabled={!letterReady || busy}
              onClick={() =>
                run(() => downloadArtifact(detail.id, "cover_letter", "pdf"), "Carta baixada.")
              }
            >
              Baixar carta (PDF)
            </button>
            <button
              type="button"
              className="button button--secondary"
              disabled={!letterReady || busy}
              onClick={() =>
                run(() => copyTextOf(artifactUrl(detail.id, "cover_letter", "txt")), "Carta copiada.")
              }
            >
              Copiar texto da carta
            </button>
          </>
        ) : null}
      </div>

      {finals.length > 0 ? (
        <section className="application-answers" aria-labelledby="answers-title">
          <h3 id="answers-title">Respostas finais</h3>
          {finals.map((q) => (
            <article key={q.id} className="application-answer">
              <p className="question__label">{q.label}</p>
              <textarea readOnly value={q.answer ?? ""} rows={3} aria-label={`Resposta: ${q.label}`} />
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  void navigator.clipboard.writeText(q.answer ?? "");
                  setStatus("Resposta copiada.");
                }}
              >
                Copiar
              </button>
            </article>
          ))}
        </section>
      ) : null}

      <p className="application-vacancy">
        {jobHref ? (
          <a href={jobHref} target="_blank" rel="noopener noreferrer">
            Abrir a vaga
          </a>
        ) : (
          <span className="muted">Link indisponível</span>
        )}
      </p>
      <p className="muted">Você envia a candidatura no site da empresa; o Rumo não envia nada.</p>

      {isApplied ? (
        <div className="application-applied">
          <p>Aplicada em {dateText(appliedAt) || "hoje"}</p>
          <button type="button" className="button button--secondary" disabled={busy} onClick={undoApplied}>
            Desfazer
          </button>
        </div>
      ) : (
        <div className="application-apply">
          <button type="button" className="button" disabled={!canApply || busy} onClick={markApplied}>
            Apliquei
          </button>
          {provisional ? <p className="muted">{PROVISIONAL_REASON}</p> : null}
        </div>
      )}

      {status ? <p role="status">{status}</p> : null}
      {error ? (
        <p role="alert" className="application-error">
          {error}
        </p>
      ) : null}
    </section>
  );
}
