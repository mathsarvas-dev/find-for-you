import { request } from "@/lib/profile";

/* Tipos espelhando api/funnel.py. Só contadores e o corte. */

export type FunnelState = {
  /** Vagas abertas que passaram nos filtros e seguem para o LLM. */
  full: number;
  /** Vagas abertas fora dos filtros (guardadas sem descrição). */
  stub: number;
  /** Vagas acima dos filtros mas abaixo do corte por similaridade. */
  gated: number;
  /** Corte ativo (0 a 1) ou null quando desligado. */
  floor: number | null;
  /** Sugestão de corte pelas vagas marcadas como boas; nunca aplicada sozinha. */
  suggested_floor: number | null;
  good_labels: number;
};

export async function getFunnel(): Promise<FunnelState> {
  return (await request<FunnelState>("/funnel")).body;
}

export async function putFunnel(floor: number | null): Promise<FunnelState> {
  return (
    await request<FunnelState>("/funnel", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ adherence_vec_floor: floor }),
    })
  ).body;
}

const count = new Intl.NumberFormat("pt-BR");

export function outsideFiltersText(n: number): string {
  if (n === 0) return "Nenhuma vaga fora dos seus filtros";
  if (n === 1) return "1 vaga fora dos seus filtros";
  return `${count.format(n)} vagas fora dos seus filtros`;
}
