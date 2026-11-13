import { ApiError, request } from "@/lib/profile";
import type { JobDetail, JobItem, JobStatusFilter } from "@/lib/sources";

/* Tipos espelhando api/matching.py e rumo_core/matching/filters.py */

export type WorkModelFilter = "onsite" | "hybrid" | "remote";
export type Seniority = "intern" | "junior" | "mid" | "senior" | "lead" | "manager" | "director";
export type JobLanguage = "pt" | "en" | "de";

export type SearchFilters = {
  countries: string[];
  work_models: WorkModelFilter[];
  seniorities: Seniority[];
  languages: JobLanguage[];
  departments: string[];
  /** Salário mínimo anualizado, na moeda `salary_currency`. */
  salary_min: number | null;
  salary_currency: string | null;
  include_unknown: boolean;
  accept_no_salary: boolean;
};

export const DEFAULT_FILTERS: SearchFilters = {
  countries: [],
  work_models: [],
  seniorities: [],
  languages: [],
  departments: [],
  salary_min: null,
  salary_currency: null,
  include_unknown: true,
  accept_no_salary: true,
};

export type UnknownField = "salary" | "work_model" | "country" | "seniority" | "language";

/** "pendente" | "ok" | "erro"; null quando a vaga nunca entrou na fila da IA. */
export type MatchState = string | null;

export type MatchJob = JobItem & {
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  salary_period: string | null;
};

export type MatchItem = {
  job: MatchJob;
  /** 0–1 (cosseno normalizado). */
  vec_score: number;
  /** 0–100, só depois da avaliação da IA. */
  ai_score: number | null;
  state: MatchState;
  unknown_fields: UnknownField[];
};

export type MatchPage = {
  total: number;
  /** null: nenhum perfil confirmado. */
  profile_version: number | null;
  /** false: o perfil ainda não tem trechos com vetor (nada é ranqueado). */
  vectorized: boolean;
  /** Vagas sob os filtros que ainda não têm vetor e ficam fora do ranking. */
  missing_embeddings: number;
  chunks_used: number;
  items: MatchItem[];
};

export type MatchDetail = JobDetail & {
  vec_score: number | null;
  ai_score: number | null;
  state: MatchState;
  rationale: string | null;
  strengths: string[] | null;
  gaps: string[] | null;
  error: string | null;
  unknown_fields: UnknownField[];
};

export type RefreshResult = {
  requested: number;
  created: number;
  reset: number;
  already_ok: number;
  profile_version: number | null;
};

export type MatchQuery = { status: JobStatusFilter; limit: number; offset: number };

/* Cliente */

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

/** Troca a mensagem genérica ("A API respondeu com erro") por uma específica. */
async function withFallback<T>(call: Promise<T>, fallback: Record<number, string>): Promise<T> {
  try {
    return await call;
  } catch (err) {
    if (
      err instanceof ApiError &&
      err.status !== null &&
      err.status in fallback &&
      err.message.startsWith("A API respondeu com erro")
    ) {
      throw new ApiError(err.status, fallback[err.status]);
    }
    throw err;
  }
}

export async function getSearchProfile(): Promise<SearchFilters> {
  return (await request<{ filters: SearchFilters }>("/search-profile")).body.filters;
}

export async function putSearchProfile(filters: SearchFilters): Promise<SearchFilters> {
  const res = await withFallback(
    request<{ filters: SearchFilters }>("/search-profile", json("PUT", filters)),
    { 422: "Confira os filtros: países como BR ou PT, e moeda junto com o salário mínimo." },
  );
  return res.body.filters;
}

export async function listMatches(query: MatchQuery): Promise<MatchPage> {
  const params = new URLSearchParams({
    status: query.status,
    limit: String(query.limit),
    offset: String(query.offset),
  });
  return (await request<MatchPage>(`/matches?${params}`)).body;
}

export async function getMatch(jobId: number): Promise<MatchDetail> {
  return (await request<MatchDetail>(`/matches/${jobId}`)).body;
}

/** Põe as 30 melhores (pelos filtros atuais) na fila da IA. 409 sem perfil; 429 se repetido em 10 s. */
export async function refreshMatches(): Promise<RefreshResult> {
  const res = await withFallback(request<RefreshResult>("/matches/refresh", { method: "POST" }), {
    429: "Aguarde alguns segundos antes de avaliar as vagas novamente.",
  });
  return res.body;
}

/* Apresentação */

export function hasPending(items: Pick<MatchItem, "state">[]): boolean {
  return items.some((i) => i.state === "pendente");
}

export type ScoreLabel = { value: number; source: "ai" | "vector"; caption: string };

/** Nota da IA (0–100) quando houver; senão a vetorial em 0–100 com a legenda "pré-seleção". */
export function scoreLabel(s: { ai_score: number | null; vec_score: number | null }): ScoreLabel | null {
  if (s.ai_score !== null) {
    return { value: clamp(Math.round(s.ai_score)), source: "ai", caption: "nota da IA" };
  }
  if (s.vec_score === null || !Number.isFinite(s.vec_score)) return null;
  return { value: clamp(Math.round(s.vec_score * 100)), source: "vector", caption: "pré-seleção" };
}

const clamp = (n: number) => Math.min(100, Math.max(0, n));

const amountFmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });

const PERIOD: Record<string, string> = { year: "/ano", month: "/mês", hour: "/h" };

type SalaryFields = Pick<
  MatchJob,
  "salary_known" | "salary_min" | "salary_max" | "salary_currency" | "salary_period"
>;

/** "BRL 8.000–12.000/mês"; null quando a vaga não informa valores. */
export function formatSalary(job: SalaryFields): string | null {
  if (!job.salary_known) return null;
  const lo = job.salary_min;
  const hi = job.salary_max;
  if (lo === null && hi === null) return null;
  const range =
    lo !== null && hi !== null && hi !== lo
      ? `${amountFmt.format(lo)}–${amountFmt.format(hi)}`
      : amountFmt.format((lo ?? hi) as number);
  const cur = job.salary_currency ? `${job.salary_currency} ` : "";
  const period = job.salary_period ? (PERIOD[job.salary_period] ?? "") : "";
  return `${cur}${range}${period}`;
}

/** Sem nenhum filtro que restrinja a lista (as opções de "desconhecido" sozinhas não restringem). */
export function isDefaultFilters(f: SearchFilters): boolean {
  return (
    f.countries.length === 0 &&
    f.work_models.length === 0 &&
    f.seniorities.length === 0 &&
    f.languages.length === 0 &&
    f.departments.length === 0 &&
    f.salary_min === null
  );
}

export const SENIORITY_LABEL: Record<Seniority, string> = {
  intern: "estágio",
  junior: "júnior",
  mid: "pleno",
  senior: "sênior",
  lead: "liderança técnica",
  manager: "gerência",
  director: "diretoria",
};

export const LANGUAGE_LABEL: Record<JobLanguage, string> = {
  pt: "português",
  en: "inglês",
  de: "alemão",
};

export function seniorityLabel(value: string): string {
  return SENIORITY_LABEL[value as Seniority] ?? "—";
}

export function languageLabel(value: string): string {
  return LANGUAGE_LABEL[value as JobLanguage] ?? "—";
}
