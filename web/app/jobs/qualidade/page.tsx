"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { LabelCard } from "@/components/quality/LabelCard";
import { QualityReport } from "@/components/quality/QualityReport";
import { PageHeader } from "@/components/shell/PageHeader";
import { ApiError } from "@/lib/profile";
import {
  deleteLabel,
  getReport,
  getSample,
  newSample,
  putLabel,
  type QualityLabel,
  type QualityReport as Report,
  type SampleItem,
} from "@/lib/quality";

type Phase =
  | { name: "loading" }
  | { name: "error"; message: string; status: number | null }
  | { name: "ready" };

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Algo deu errado ao falar com a API. Tente de novo.";

/** 409 com perfil existente, mas ainda sem vetor (o worker está processando). */
const isNotVectorized = (status: number | null, text: string) =>
  status === 409 && text.includes("sendo processado");

const firstUnlabeled = (items: SampleItem[]) => {
  const i = items.findIndex((it) => it.label === null);
  return i === -1 ? 0 : i;
};

export default function QualityPage() {
  const [phase, setPhase] = useState<Phase>({ name: "loading" });
  const [bootKey, setBootKey] = useState(0);
  const [items, setItems] = useState<SampleItem[]>([]);
  const [index, setIndex] = useState(0);
  const [report, setReport] = useState<Report | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** Trava síncrona: dois cliques seguidos não mandam dois pedidos. */
  const busyRef = useRef(false);
  const reportSeq = useRef(0);
  /** Identidade da amostra na tela: sobe a cada amostra nova; gravações antigas não mexem na nova. */
  const sampleSeq = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const loadReport = useCallback(async () => {
    const id = ++reportSeq.current;
    try {
      const r = await getReport();
      if (!mounted.current || id !== reportSeq.current) return;
      setReport(r);
      setReportError(null);
    } catch (err) {
      if (!mounted.current || id !== reportSeq.current) return;
      // 409: o perfil perdeu o vetor (ou sumiu); o relatório antigo não vale mais.
      if (err instanceof ApiError && err.status === 409) setReport(null);
      setReportError(message(err));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    getSample().then(
      (sample) => {
        if (cancelled) return;
        sampleSeq.current++;
        setItems(sample.items);
        setIndex(firstUnlabeled(sample.items));
        setPhase({ name: "ready" });
        void loadReport();
      },
      (err) => {
        if (cancelled) return;
        setPhase({
          name: "error",
          message: message(err),
          status: err instanceof ApiError ? err.status : null,
        });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [bootKey, loadReport]);

  /** Roda uma gravação por vez; erro mostra a mensagem e deixa a vaga onde está. */
  async function guarded(work: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setActionError(null);
    try {
      await work();
    } catch (err) {
      if (mounted.current) setActionError(message(err));
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  const current: SampleItem | undefined = items[index];

  function setLabel(jobId: number, label: QualityLabel | null) {
    setItems((prev) => prev.map((it) => (it.job_id === jobId ? { ...it, label } : it)));
  }

  function label(value: QualityLabel) {
    if (!current) return;
    const { job_id: id } = current;
    const at = index;
    const seq = sampleSeq.current;
    void guarded(async () => {
      await putLabel(id, value);
      if (!mounted.current) return;
      // A amostra trocou durante a gravação: o índice e a lista novos não são desta vaga.
      if (seq !== sampleSeq.current) return;
      setLabel(id, value);
      setIndex(Math.min(at + 1, items.length - 1));
      void loadReport();
    });
  }

  function clear() {
    if (!current) return;
    const { job_id: id } = current;
    const seq = sampleSeq.current;
    void guarded(async () => {
      await deleteLabel(id);
      if (!mounted.current) return;
      if (seq !== sampleSeq.current) return;
      setLabel(id, null);
      void loadReport();
    });
  }

  /** Pela mesma trava das gravações: nenhuma gravação fica no ar enquanto a amostra troca. */
  function renewSample() {
    return guarded(async () => {
      const sample = await newSample();
      if (!mounted.current) return;
      sampleSeq.current++;
      setItems(sample.items);
      setIndex(firstUnlabeled(sample.items));
      await loadReport();
    });
  }

  const labeled = items.filter((it) => it.label !== null).length;

  return (
    <main className="page">
      <PageHeader
        title="Teste de qualidade"
        description="Dê um rótulo às vagas da amostra para medir se o ranking põe as boas no topo."
        actions={
          <Link className="button button--secondary" href="/jobs">
            Voltar às vagas
          </Link>
        }
      />

      {phase.name === "loading" && (
        <section className="panel" aria-busy="true" aria-label="Carregando amostra">
          <div className="panel__body stack">
            <p className="muted">Buscando a amostra…</p>
            <span className="skeleton" aria-hidden="true" />
          </div>
        </section>
      )}

      {phase.name === "error" && (
        <section className="panel">
          <div className="notice notice--top" role="alert">
            <strong>Não deu para carregar a amostra.</strong>
            <p>{phase.message}</p>
            {isNotVectorized(phase.status, phase.message) && (
              <p>Seu perfil ainda está sendo vetorizado; isso leva só alguns instantes.</p>
            )}
          </div>
          <div className="panel__foot">
            {phase.status === 409 && !isNotVectorized(phase.status, phase.message) ? (
              <Link className="button" href="/onboarding">
                Ir para o perfil
              </Link>
            ) : (
              <button
                type="button"
                className="button"
                onClick={() => {
                  setPhase({ name: "loading" });
                  setBootKey((k) => k + 1);
                }}
              >
                Tentar de novo
              </button>
            )}
          </div>
        </section>
      )}

      {phase.name === "ready" && (
        <div className="quality stack">
          {actionError && (
            <div className="notice" role="alert">
              <strong>Não consegui concluir.</strong>
              <p>{actionError}</p>
            </div>
          )}
          {items.length === 0 ? (
            <section className="panel">
              <div className="panel__body">
                <p className="empty">A amostra está vazia: ainda não há vagas para rotular.</p>
              </div>
            </section>
          ) : (
            <section className="panel" aria-label="Rotular vagas">
              <div className="panel__head">
                <h2>Rotular</h2>
                <span className="mono" aria-live="polite">
                  {labeled} de {items.length} rotuladas
                </span>
              </div>
              {current && (
                <div className="panel__body">
                  <LabelCard
                    key={current.job_id}
                    item={current}
                    busy={busy}
                    canPrevious={index > 0}
                    canSkip={index < items.length - 1}
                    onLabel={label}
                    onSkip={() => setIndex((i) => Math.min(i + 1, items.length - 1))}
                    onPrevious={() => setIndex((i) => Math.max(i - 1, 0))}
                    onClear={clear}
                  />
                </div>
              )}
            </section>
          )}

          {report && <QualityReport report={report} onNewSample={renewSample} busy={busy} />}
          {!report && reportError && (
            <div className="notice" role="alert">
              <strong>Não deu para carregar o relatório.</strong>
              <p>{reportError}</p>
              <button type="button" className="button button--secondary" onClick={() => void loadReport()}>
                Tentar de novo
              </button>
            </div>
          )}
        </div>
      )}
    </main>
  );
}
