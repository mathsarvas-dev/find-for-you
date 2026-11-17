import { request } from "@/lib/profile";

/* Tipos espelhando api/pipeline.py e api/highlights.py. */

export type Stage = "saved" | "preparing" | "applied" | "interview" | "offer" | "rejected" | "no_response";

/** Ordem das colunas do quadro. */
export const STAGES: Stage[] = ["saved", "preparing", "applied", "interview", "offer", "rejected", "no_response"];

export const STAGE_LABEL: Record<Stage, string> = {
  saved: "Salva",
  preparing: "Preparando",
  applied: "Aplicada",
  interview: "Entrevista",
  offer: "Oferta",
  rejected: "Recusada",
  no_response: "Sem resposta",
};

/** Etapas em que não há mais o que acompanhar (sem lembrete). */
export const CLOSED_STAGES: Stage[] = ["rejected", "no_response"];

export type PipelineItem = {
  id: number;
  job_id: number;
  job_title: string;
  company_name: string;
  stage: Stage;
  state: string;
  ai_score: number | null;
  applied_at: string | null;
  follow_up_at: string | null;
  notes: string | null;
};

export type PipelineOut = { items: PipelineItem[]; today: string };

export type PipelinePatch = {
  stage?: Stage;
  follow_up_at?: string | null;
  notes?: string | null;
};

export type ReportGroup = { key: string; applied: number; answered: number; rate: number | null };

export type Report = {
  found: number;
  saved: number;
  applied: number;
  answered: number;
  response_rate: number | null;
  by_country: ReportGroup[];
  by_source: ReportGroup[];
  by_score_band: ReportGroup[];
};

export type HighlightItem = { job_id: number; job_title: string; company_name: string; ai_score: number };

export type Highlights = {
  min_score: number;
  days: number;
  new_high_count: number;
  items: HighlightItem[];
  follow_ups_due: number;
};

export const MAX_NOTES = 2000;

/** Situação do lembrete frente a "hoje" (ambos AAAA-MM-DD): comparação de texto basta. */
export type FollowUpState = "overdue" | "today" | "upcoming";

export function followUpState(date: string, today: string): FollowUpState {
  if (date < today) return "overdue";
  return date === today ? "today" : "upcoming";
}

/** "2026-12-01" -> "01/12/2026", sem passar por Date (sem fuso). */
export function formatDay(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export function getPipeline(): Promise<PipelineOut> {
  return request<PipelineOut>("/pipeline").then((r) => r.body);
}

export function patchPipeline(id: number, patch: PipelinePatch): Promise<PipelineItem> {
  return request<PipelineItem>(`/pipeline/${id}`, {
    method: "PATCH",
    headers: JSON_HEADERS,
    body: JSON.stringify(patch),
  }).then((r) => r.body);
}

export function getReport(): Promise<Report> {
  return request<Report>("/pipeline/report").then((r) => r.body);
}

export function getHighlights(signal?: AbortSignal): Promise<Highlights> {
  return request<Highlights>("/highlights", signal ? { signal } : undefined).then((r) => r.body);
}
