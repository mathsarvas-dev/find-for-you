"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { applicationSteps, type ApplicationView } from "@/components/applications/applicationSteps";
import { RequirementsStep } from "@/components/applications/RequirementsStep";
import { useRefreshOverview } from "@/components/shell/OverviewContext";
import { PageHeader } from "@/components/shell/PageHeader";
import { Stepper } from "@/components/stepper/Stepper";
import {
  generateApplication,
  getApplication,
  type ApplicationDetail,
} from "@/lib/applications";
import { ApiError } from "@/lib/profile";

const POLL_MS = 3000;

const VIEWS: { id: ApplicationView; label: string }[] = [
  { id: "read", label: "Ler a vaga" },
  { id: "resume", label: "Currículo" },
  { id: "questions", label: "Perguntas" },
  { id: "kit", label: "Kit" },
];

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : "Algo deu errado. Tente de novo.";
}

const WARNING_TEXT: Record<ApplicationDetail["warnings"][number], string> = {
  profile_changed: "O perfil mudou desde esta versão.",
  job_changed: "A vaga mudou desde que o currículo foi gerado.",
  job_closed: "A vaga foi encerrada.",
};

export default function CandidaturaPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const refreshOverview = useRefreshOverview();

  const [detail, setDetail] = useState<ApplicationDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [view, setView] = useState<ApplicationView>("read");
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getApplication(id)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        setLoadError(null);
        setView((current) => (current === "read" && d.resume ? "resume" : current));
      })
      .catch((e: unknown) => {
        if (!cancelled) setLoadError(messageOf(e));
      });
    return () => {
      cancelled = true;
    };
  }, [id, reloadKey]);

  // polling while the worker generates or a draft is queued (stops when neither)
  const polling =
    detail !== null &&
    (detail.state === "generating" || (detail.tasks ?? []).some((t) => t.state === "queued"));
  useEffect(() => {
    if (!polling) return;
    const timer = setInterval(() => setReloadKey((k) => k + 1), POLL_MS);
    return () => clearInterval(timer);
  }, [polling]);

  async function regenerate() {
    setBusy(true);
    setActionError(null);
    try {
      await generateApplication(id);
      setReloadKey((k) => k + 1);
      refreshOverview();
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  if (detail === null) {
    return (
      <>
        <PageHeader title="Candidatura" />
        {loadError ? (
          <div role="alert" className="application-error">
            <p>{loadError}</p>
            <button type="button" className="button button--secondary" onClick={() => setReloadKey((k) => k + 1)}>
              Tentar de novo
            </button>
          </div>
        ) : (
          <p className="muted">Carregando a candidatura…</p>
        )}
      </>
    );
  }

  const steps = applicationSteps(detail, view);
  const canRegenerate = detail.can_generate.allowed && !busy;

  return (
    <>
      <PageHeader
        title={`Candidatura · ${detail.job.title}`}
        description={`${detail.job.company_name} · ${detail.job.status === "open" ? "vaga aberta" : "vaga encerrada"}`}
      />
      <Link href="/candidaturas" className="link-button">
        Todas as candidaturas
      </Link>

      <Stepper steps={steps} orientation="horizontal" label="Trilha da candidatura" />

      {detail.warnings.map((w) => (
        <div key={w} role="status" className="application-warning">
          <p>{WARNING_TEXT[w]}</p>
          {w === "profile_changed" ? (
            <button
              type="button"
              className="button button--secondary"
              disabled={!canRegenerate}
              title={detail.can_generate.reason ?? undefined}
              onClick={regenerate}
            >
              Gerar de novo
            </button>
          ) : null}
        </div>
      ))}

      {detail.state === "generating" ? (
        <p role="status" className="muted">Gerando o currículo…</p>
      ) : null}

      {detail.state === "error" ? (
        <div role="alert" className="application-error">
          <p>{detail.error ?? "Não foi possível gerar a candidatura."}</p>
          <button
            type="button"
            className="button button--secondary"
            disabled={!canRegenerate}
            title={detail.can_generate.reason ?? undefined}
            onClick={regenerate}
          >
            Gerar de novo
          </button>
          {detail.can_generate.reason ? <p className="muted">{detail.can_generate.reason}</p> : null}
        </div>
      ) : null}

      {actionError ? (
        <p role="alert" className="application-error">
          {actionError}
        </p>
      ) : null}

      <div role="group" aria-label="Visão da candidatura" className="application-views">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            type="button"
            className={v.id === view ? "button" : "button button--secondary"}
            aria-pressed={v.id === view}
            onClick={() => setView(v.id)}
          >
            {v.label}
          </button>
        ))}
      </div>

      {view === "read" ? <RequirementsStep detail={detail} /> : null}
      {view === "resume" ? (
        <p className="muted">Revisão do currículo chega no próximo passo.</p>
      ) : null}
      {view === "questions" ? (
        <p className="muted">Perguntas da vaga chegam no próximo passo.</p>
      ) : null}
      {view === "kit" ? (
        <p className="muted">O kit fica pronto depois da revisão do currículo.</p>
      ) : null}
    </>
  );
}
