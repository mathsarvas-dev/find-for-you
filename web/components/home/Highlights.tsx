"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getHighlights, type Highlights as HighlightsData } from "@/lib/pipeline";

const num = new Intl.NumberFormat("pt-BR");

/** "3 vagas novas com nota 80 ou mais" (só o que mudou nos últimos dias; nada sai do app). */
export function newJobsText(h: HighlightsData): string {
  const n = h.new_high_count;
  return `${num.format(n)} ${n === 1 ? "vaga nova" : "vagas novas"} com nota ${h.min_score} ou mais`;
}

export function followUpsText(n: number): string {
  return n === 1 ? "1 follow-up para hoje ou atrasado" : `${num.format(n)} follow-ups para hoje ou atrasados`;
}

/**
 * Destaques do Início. Busca sozinho; sem destaque (ou com falha) não mostra nada:
 * é um extra, nunca um erro na tela inicial.
 */
export function Highlights() {
  const [data, setData] = useState<HighlightsData | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getHighlights(controller.signal)
      .then(setData)
      .catch(() => {});
    return () => controller.abort();
  }, []);

  if (data === null || (data.new_high_count === 0 && data.follow_ups_due === 0)) return null;

  return (
    <section className="panel highlights" aria-labelledby="destaques">
      <div className="panel__head">
        <h2 id="destaques">Destaques</h2>
      </div>
      <div className="highlights__body">
        {data.new_high_count > 0 ? (
          <div>
            <p className="highlights__headline">
              {newJobsText(data)} nos últimos {data.days} dias.
            </p>
            <ul className="highlights__list">
              {data.items.map((item) => (
                <li key={item.job_id}>
                  {item.job_title} · {item.company_name} · Nota {item.ai_score}
                </li>
              ))}
            </ul>
            <Link href="/jobs">Ver vagas</Link>
          </div>
        ) : null}
        {data.follow_ups_due > 0 ? (
          <div>
            <p className="highlights__headline">{followUpsText(data.follow_ups_due)}.</p>
            <Link href="/pipeline">Abrir pipeline</Link>
          </div>
        ) : null}
        <p className="muted">Estes avisos aparecem só aqui, no app; nada é enviado para fora.</p>
      </div>
    </section>
  );
}
