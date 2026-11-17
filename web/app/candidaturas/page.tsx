"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { listApplications, type ApplicationListItem, type ApplicationState } from "@/lib/applications";

const STATE_TEXT: Record<ApplicationState, string> = {
  draft: "Rascunho",
  generating: "Gerando o currículo",
  ready: "Pronta para revisar",
  error: "Com erro",
  applied: "Aplicada",
};

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : "Algo deu errado. Tente de novo.";
}

export default function CandidaturasPage() {
  const [items, setItems] = useState<ApplicationListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    listApplications()
      .then((body) => {
        if (cancelled) return;
        setItems(body.items);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(messageOf(e));
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  return (
    <>
      <PageHeader title="Candidaturas" description="Vagas com currículo preparado, em revisão ou aplicadas." />

      {error ? (
        <div role="alert" className="application-error">
          <p>{error}</p>
          <button type="button" className="button button--secondary" onClick={() => setReloadKey((k) => k + 1)}>
            Tentar de novo
          </button>
        </div>
      ) : null}

      {!error && items === null ? <p className="muted">Carregando as candidaturas…</p> : null}

      {items !== null && items.length === 0 ? (
        <div className="application-empty">
          <p>Nenhuma candidatura ainda. Prepare uma a partir de uma vaga avaliada.</p>
          <Link href="/jobs" className="button">
            Ver vagas
          </Link>
        </div>
      ) : null}

      {items !== null && items.length > 0 ? (
        <ul className="application-list-page">
          {items.map((app) => (
            <li key={app.id} className="application-row">
              <Link href={`/candidaturas/${app.id}`}>
                {app.job_title} · {app.company_name}
              </Link>
              <p className="muted">
                {STATE_TEXT[app.state]}
                {app.coverage ? ` · ${app.coverage.must.covered} de ${app.coverage.must.total} obrigatórios` : ""}
              </p>
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
