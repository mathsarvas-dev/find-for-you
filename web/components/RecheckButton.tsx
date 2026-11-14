"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { useRefreshOverview } from "@/components/shell/OverviewContext";

/**
 * "Verificar de novo" do painel de saúde: router.refresh() pede ao servidor um novo
 * payload da rota, o que roda de novo o HealthPanel (connection() + checkHealth, sem
 * cache). Dentro de uma transição: o painel atual fica na tela e o botão mostra
 * "Verificando…" até a resposta nova chegar. Não recebe dados do servidor.
 */
export function RecheckButton() {
  const router = useRouter();
  const refreshOverview = useRefreshOverview();
  const [pending, startTransition] = useTransition();

  return (
    <>
      <span className="sr-only" role="status" aria-live="polite">
        {pending ? "Verificando a API e o banco…" : ""}
      </span>
      <button
        type="button"
        className="button"
        disabled={pending}
        aria-busy={pending || undefined}
        onClick={() => {
          if (pending) return;
          // A casca também lê /overview: sem isso a lateral e a conta continuariam mostrando a falha.
          refreshOverview();
          startTransition(() => router.refresh());
        }}
      >
        {pending ? "Verificando…" : "Verificar de novo"}
      </button>
    </>
  );
}
