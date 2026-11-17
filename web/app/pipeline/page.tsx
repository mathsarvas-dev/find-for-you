"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PipelineBoard } from "@/components/pipeline/PipelineBoard";
import { PipelineReport } from "@/components/pipeline/PipelineReport";
import { PageHeader } from "@/components/shell/PageHeader";
import { useRefreshOverview } from "@/components/shell/OverviewContext";
import {
  getPipeline,
  getReport,
  patchPipeline,
  type PipelineItem,
  type PipelinePatch,
  type Report,
} from "@/lib/pipeline";

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : "Algo deu errado. Tente de novo.";
}

export default function PipelinePage() {
  const refreshOverview = useRefreshOverview();
  const [items, setItems] = useState<PipelineItem[] | null>(null);
  const [today, setToday] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [view, setView] = useState<"board" | "report">("board");

  useEffect(() => {
    let cancelled = false;
    Promise.all([getPipeline(), getReport()])
      .then(([board, rep]) => {
        if (cancelled) return;
        setItems(board.items);
        setToday(board.today);
        setReport(rep);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(messageOf(e));
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const onPatch = useCallback(
    async (id: number, patch: PipelinePatch) => {
      const updated = await patchPipeline(id, patch);
      setItems((cur) => (cur ? cur.map((i) => (i.id === id ? updated : i)) : cur));
      // o relatório e o resumo do Início dependem da etapa
      getReport()
        .then(setReport)
        .catch(() => {});
      void refreshOverview();
    },
    [refreshOverview],
  );

  return (
    <>
      <PageHeader title="Pipeline" description="Acompanhe cada vaga salva, da preparação até a resposta." />

      {error ? (
        <div role="alert" className="application-error">
          <p>{error}</p>
          <button type="button" className="button button--secondary" onClick={() => setReloadKey((k) => k + 1)}>
            Tentar de novo
          </button>
        </div>
      ) : null}

      {!error && items === null ? <p className="muted">Carregando o pipeline…</p> : null}

      {items !== null && items.length === 0 ? (
        <div className="application-empty">
          <p>
            Nenhuma vaga no pipeline. Na lista de vagas, selecione uma e aperte <kbd>s</kbd> para salvá-la.
          </p>
          <Link href="/jobs" className="button">
            Ver vagas
          </Link>
        </div>
      ) : null}

      {items !== null && items.length > 0 ? (
        <>
          <div className="pipeline-tabs" role="group" aria-label="Visão">
            <button
              type="button"
              className="button button--secondary"
              aria-pressed={view === "board"}
              onClick={() => setView("board")}
            >
              Quadro
            </button>
            <button
              type="button"
              className="button button--secondary"
              aria-pressed={view === "report"}
              onClick={() => setView("report")}
            >
              Relatório
            </button>
          </div>
          {view === "board" ? (
            <PipelineBoard items={items} today={today} onPatch={onPatch} />
          ) : report ? (
            <PipelineReport report={report} />
          ) : null}
        </>
      ) : null}
    </>
  );
}
