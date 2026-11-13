import { request } from "@/lib/profile";

/* Tipos espelhando api/overview.py (OverviewOut). Só contadores e flags. */

export type ProfileOverview = {
  confirmed: boolean;
  version: number | null;
  /** Pelo menos um trecho do perfil atual vetorizado no modelo ativo. */
  vectorized: boolean;
  name: string | null;
  email: string | null;
};

/** "in_progress": alguma sessão não concluída (inclusive com erro, que pode ser retomada). */
export type InterviewState = "none" | "in_progress" | "done";

export type InterviewOverview = { state: InterviewState };

export type CompaniesOverview = { total: number; active: number; auto: number };

export type JobsOverview = { open: number };

/** Só vagas abertas, na versão atual do perfil. */
export type MatchesOverview = { scored: number; pending: number };

export type Overview = {
  profile: ProfileOverview;
  interview: InterviewOverview;
  companies: CompaniesOverview;
  jobs: JobsOverview;
  matches: MatchesOverview;
};

/** Mesmos padrões seguros da API (usuário sem nada). */
export function emptyOverview(): Overview {
  return {
    profile: { confirmed: false, version: null, vectorized: false, name: null, email: null },
    interview: { state: "none" },
    companies: { total: 0, active: 0, auto: 0 },
    jobs: { open: 0 },
    matches: { scored: 0, pending: 0 },
  };
}

export async function getOverview(): Promise<Overview> {
  return (await request<Overview>("/overview")).body;
}
