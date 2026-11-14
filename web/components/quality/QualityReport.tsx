"use client";

import { useState } from "react";
import type { QualityReport as Report } from "@/lib/quality";

type Props = {
  report: Report;
  /** Pede outra amostra; rejeitar mantém o relatório e a página mostra o erro. */
  onNewSample: () => Promise<void> | void;
  /** Há uma gravação em andamento na página: "Nova amostra" espera. */
  busy?: boolean;
};

const fmt = (n: number | null) => (n === null ? "—" : n.toFixed(2).replace(".", ","));

const REASON = {
  good: "boa fora das 10 primeiras",
  bad: "ruim entre as 10 primeiras",
} as const;

export function QualityReport({ report, onNewSample, busy = false }: Props) {
  const [asking, setAsking] = useState(false);

  async function newSample() {
    if (asking) return;
    if (!window.confirm("Gerar uma nova amostra? Os rótulos que você já deu ficam guardados.")) return;
    setAsking(true);
    try {
      await onNewSample();
    } finally {
      setAsking(false);
    }
  }

  const valid = report.status !== "insuficiente";
  return (
    <section className="panel" aria-label="Relatório de qualidade">
      <div className="panel__head">
        <h2>Relatório</h2>
      </div>
      <div className="panel__body stack">
        {!valid ? (
          <>
            <p>Ainda não dá para medir a qualidade:</p>
            <ul className="quality-list">
              {report.missing.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </>
        ) : (
          <>
            <p className={`quality-verdict quality-verdict--${report.status}`}>
              {report.status === "dentro_da_meta"
                ? "dentro da meta"
                : `abaixo da meta (meta ${String(report.target).replace(".", ",")})`}
            </p>
            <dl className="quality-stats">
              <div>
                <dt>Precision@10 atual</dt>
                <dd className="mono">{fmt(report.precision_at_10)}</dd>
              </div>
              <div>
                <dt>Precision@10 do funil</dt>
                <dd className="mono">{fmt(report.precision_vec_at_10)}</dd>
              </div>
              <div>
                <dt>Amostra</dt>
                <dd className="mono">
                  {report.labeled} rotuladas ({report.good} boas, {report.bad} ruins)
                </dd>
              </div>
            </dl>
            {report.misranked.length > 0 && (
              <>
                <h3 className="quality-subhead">Mal classificadas</h3>
                <ul className="quality-list">
                  {report.misranked.map((m) => (
                    <li key={m.job_id}>
                      <span className="mono">#{m.position}</span> {m.title}{" "}
                      <span className="muted">({REASON[m.label]})</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>
      <div className="panel__foot">
        <button
          type="button"
          className="button button--secondary"
          disabled={asking || busy}
          aria-busy={asking || undefined}
          onClick={() => void newSample()}
        >
          Nova amostra
        </button>
      </div>
    </section>
  );
}
