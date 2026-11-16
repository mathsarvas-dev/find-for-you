import { ApiError, request } from "@/lib/profile";

/* Tipos espelhando api/sources.py */

export type Company = {
  id: number;
  source: string;
  identifier: string;
  name: string;
  country: string | null;
  active: boolean;
  last_run_at: string | null;
  last_success_at: string | null;
  next_run_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  open_jobs: number;
  /** "auto": adicionada pela descoberta; "feed"/"link": sintética (não listada aqui). */
  origin: CompanyOrigin;
  /** "funnel": filtros da pessoa; "all": ignora os filtros só para esta empresa. */
  collect_mode: CollectMode;
  /** Vagas abertas que ficaram fora dos filtros (guardadas como stub). */
  stub_jobs: number;
};

export type CompanyOrigin = "manual" | "auto" | "feed" | "link";

export type CollectMode = "funnel" | "all";

/** Fontes de quadro (ATS) verificadas, mais "site" (página de carreira). */
export type CompanySource = "greenhouse" | "lever" | "ashby" | "smartrecruiters" | "breezy" | "site" | string;

export type NewCompany = {
  /** Sem fonte: a API detecta pela URL (ou usa "site" para outra URL http(s)). */
  source?: CompanySource;
  identifier: string;
  name?: string;
  country?: string;
  collect_mode?: CollectMode;
};

export type CompanyPatch = {
  active?: boolean;
  name?: string;
  country?: string | null;
  collect_mode?: CollectMode;
};

/** onsite | hybrid | remote | unknown */
export type WorkModel = string;

export type JobItem = {
  id: number;
  company_id: number;
  company_name: string;
  title: string;
  url: string;
  location_text: string;
  country: string | null;
  city: string | null;
  work_model: WorkModel;
  seniority: string;
  language: string;
  department: string | null;
  salary_known: boolean;
  status: string;
  first_seen_at: string;
  posted_at: string | null;
  /** "feed": vaga vinda de um agregador (com crédito obrigatório). */
  origin: string;
  /** Fonte da empresa (ex.: "greenhouse", "remotive"). */
  source: string;
  attribution: { label: string; url: string } | null;
};

export type JobDetail = JobItem & {
  description: string;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  salary_period: string | null;
  last_seen_at: string;
  closed_at: string | null;
};

export type JobPage = { total: number; items: JobItem[] };

export type JobStatusFilter = "open" | "closed" | "all";

export type JobQuery = {
  status: JobStatusFilter;
  companyId: number | null;
  limit: number;
  offset: number;
  q?: string;
};

/** Mesmo limite de `q` na API. */
export const MAX_QUERY_CHARS = 100;

const SOURCE_LABELS: Record<string, string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
  smartrecruiters: "SmartRecruiters",
  breezy: "Breezy",
  site: "Página de carreira",
};

/** Nome de exibição de uma fonte; fonte desconhecida mostra o próprio nome. */
export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

/* Cliente */

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export async function listCompanies(origin?: CompanyOrigin): Promise<Company[]> {
  const path = origin ? `/companies?${new URLSearchParams({ origin })}` : "/companies";
  return (await request<Company[]>(path)).body;
}

/** Mensagens para quando o servidor não manda um `detail` em texto. */
const ADD_FALLBACK: Record<number, string> = {
  409: "Essa empresa já está cadastrada.",
  422: "Confira o token do quadro e o país (2 letras, ex.: BR).",
  502: "O Greenhouse respondeu de um jeito inesperado. Tente de novo mais tarde.",
  503: "O Greenhouse não respondeu agora. Tente de novo em alguns minutos.",
};

/** Valida o quadro no Greenhouse (pode levar alguns segundos) e cadastra a empresa. */
export async function addCompany(input: NewCompany): Promise<Company> {
  try {
    return (await request<Company>("/companies", json("POST", input))).body;
  } catch (err) {
    if (
      err instanceof ApiError &&
      err.status !== null &&
      err.status in ADD_FALLBACK &&
      err.message.startsWith("A API respondeu com erro")
    ) {
      throw new ApiError(err.status, ADD_FALLBACK[err.status]);
    }
    throw err;
  }
}

export async function patchCompany(id: number, patch: CompanyPatch): Promise<Company> {
  return (await request<Company>(`/companies/${id}`, json("PATCH", patch))).body;
}

/** 409 quando a empresa está pausada. */
export async function collectNow(id: number): Promise<Company> {
  return (await request<Company>(`/companies/${id}/collect-now`, { method: "POST" })).body;
}

export async function listJobs(query: JobQuery): Promise<JobPage> {
  const params = new URLSearchParams({
    status: query.status,
    limit: String(query.limit),
    offset: String(query.offset),
  });
  if (query.companyId !== null) params.set("company_id", String(query.companyId));
  const q = (query.q ?? "").trim().slice(0, MAX_QUERY_CHARS);
  if (q) params.set("q", q);
  return (await request<JobPage>(`/jobs?${params}`)).body;
}

export async function getJob(id: number): Promise<JobDetail> {
  return (await request<JobDetail>(`/jobs/${id}`)).body;
}

/* Apresentação */

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

const dateFmt = new Intl.DateTimeFormat("pt-BR", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  timeZone: "UTC",
});

/** "há 5 min", "há 3 h", "há 2 dias"; depois de 30 dias, a data. `now` vem do cliente. */
export function relativeTime(iso: string, now: number): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const diff = Math.max(0, now - t);
  if (diff < MIN) return "agora há pouco";
  if (diff < HOUR) return `há ${Math.floor(diff / MIN)} min`;
  if (diff < DAY) return `há ${Math.floor(diff / HOUR)} h`;
  const days = Math.floor(diff / DAY);
  if (days < 30) return `há ${days} ${days === 1 ? "dia" : "dias"}`;
  return dateFmt.format(t);
}

/** Ativa e esperando coleta: nunca coletada ou com a próxima coleta já vencida. */
export function isPending(c: Company, now: number): boolean {
  if (!c.active) return false;
  if (c.last_run_at === null) return true;
  return c.next_run_at !== null && Date.parse(c.next_run_at) <= now;
}

/** A URL só vira link se for http(s); qualquer outro esquema (javascript:, data:) é descartado. */
export function safeJobUrl(raw: string): string | null {
  try {
    const url = new URL(raw.trim());
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

const WORK_MODEL: Record<string, string> = {
  remote: "remoto",
  hybrid: "híbrido",
  onsite: "presencial",
};

export function workModelLabel(value: WorkModel): string {
  return WORK_MODEL[value] ?? "—";
}
