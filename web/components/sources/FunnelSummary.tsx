"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/profile";
import { getFunnel, outsideFiltersText, putFunnel, type FunnelState } from "@/lib/funnel";

const decimal = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 3 });

const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Não consegui atualizar o corte. Tente de novo.";

/**
 * Resumo do funil na aba Empresas: vagas fora dos filtros e o corte por similaridade.
 * O corte nunca liga sozinho: a sugestão só vale quando a pessoa clica em "Ativar corte".
 */
export function FunnelSummary() {
  const [state, setState] = useState<FunnelState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    getFunnel()
      .then((s) => {
        if (mounted.current) setState(s);
      })
      .catch((err) => {
        if (mounted.current) setError(message(err));
      });
    return () => {
      mounted.current = false;
    };
  }, []);

  async function change(floor: number | null) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const next = await putFunnel(floor);
      if (mounted.current) setState(next);
    } catch (err) {
      if (mounted.current) setError(message(err));
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  if (error && !state) {
    return (
      <div className="notice" role="alert">
        <p>{error}</p>
      </div>
    );
  }
  if (!state) return null;

  const suggestion = state.suggested_floor;
  return (
    <section className="funnel-summary" aria-label="Funil de aderência">
      <p className="funnel-summary__count">{outsideFiltersText(state.stub)}</p>
      {state.floor === null && suggestion !== null && (
        <div className="funnel-summary__row">
          <p>
            Sugestão de corte por similaridade: {decimal.format(suggestion)} (mantém 95% das vagas que
            você marcou como boas).
          </p>
          <button
            type="button"
            className="button button--secondary"
            disabled={busy}
            aria-busy={busy || undefined}
            onClick={() => void change(suggestion)}
          >
            Ativar corte
          </button>
        </div>
      )}
      {state.floor !== null && (
        <div className="funnel-summary__row">
          <p>Corte ativo: {decimal.format(state.floor)}</p>
          <button
            type="button"
            className="button button--secondary"
            disabled={busy}
            aria-busy={busy || undefined}
            onClick={() => void change(null)}
          >
            Desligar corte
          </button>
        </div>
      )}
      <p className="field__hint">
        O corte economiza avaliações, mas pode esconder vagas boas; vagas cortadas podem voltar se
        você desligar.
      </p>
      {error && (
        <p className="field__error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
