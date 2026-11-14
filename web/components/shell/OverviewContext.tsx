"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { getOverview, type Overview } from "@/lib/overview";

/** "loading": nenhuma resposta ainda; "ok": a última busca respondeu; "down": a última busca falhou. */
export type OverviewStatus = "loading" | "ok" | "down";

export type OverviewState = {
  /** Último overview recebido (mantido quando uma busca posterior falha); null até o primeiro. */
  overview: Overview | null;
  status: OverviewStatus;
  /**
   * Busca de novo (ex.: depois de confirmar o perfil ou cadastrar uma empresa).
   * A promessa termina quando essa busca termina (com ou sem sucesso) ou é substituída;
   * nunca rejeita.
   */
  refresh: () => Promise<void>;
};

const noop = async () => {};

export const OverviewContext = createContext<OverviewState>({
  overview: null,
  status: "loading",
  refresh: noop,
});

/** Estado do overview da casca. Fora da casca: vazio, e refresh() não faz nada. */
export function useOverview(): OverviewState {
  return useContext(OverviewContext);
}

/**
 * refresh() do overview em "dispara e esquece", para depois de uma ação que muda os
 * números da casca (confirmar perfil, cadastrar empresa, avaliar vagas…): não espera
 * e ignora qualquer erro. Estável enquanto o refresh da casca for.
 */
export function useRefreshOverview(): () => void {
  const { refresh } = useOverview();
  return useCallback(() => {
    try {
      Promise.resolve(refresh()).catch(() => {});
    } catch {
      // Nunca deixa a atualização do resumo derrubar a ação da página.
    }
  }, [refresh]);
}

/**
 * Carrega o overview para a casca, de novo a cada mudança de `key` (a rota) e em refresh().
 * Só a busca mais recente pode atualizar o estado: a anterior é abortada e, se ainda
 * assim responder, é ignorada (número de sequência). Nada é atualizado depois de desmontar.
 * Falhas são silenciosas: viram status "down".
 */
export function useOverviewLoader(key: string): OverviewState {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [status, setStatus] = useState<OverviewStatus>("loading");
  const seq = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    const id = ++seq.current;
    const fresh = () => id === seq.current && !current.signal.aborted;
    return getOverview(current.signal).then(
      (data) => {
        if (!fresh()) return;
        setOverview(data);
        setStatus("ok");
      },
      () => {
        if (fresh()) setStatus("down");
      },
    );
  }, []);

  useEffect(() => {
    load();
  }, [key, load]);

  // Ao desmontar: aborta e invalida qualquer resposta pendente.
  useEffect(
    () => () => {
      seq.current += 1;
      controller.current?.abort();
    },
    [],
  );

  return { overview, status, refresh: load };
}
