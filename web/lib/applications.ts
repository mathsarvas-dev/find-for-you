import { request } from "@/lib/profile";

/* Tipos espelhando api/applications.py e rumo_core/applications/lines.py */

export type ApplicationState = "draft" | "generating" | "ready" | "error" | "applied";
export type LineOrigin = "fact" | "ai" | "user";
export type DocStatus = "valid" | "needs_fix";
export type DocKind = "resume" | "cover_letter";
export type Template = "us" | "de" | "au" | "br";

export type DocLine = {
  id: string;
  section: string;
  text: string;
  fields: Record<string, string>;
  parent: string | null;
  source_refs: string[];
  origin: LineOrigin;
  covers: string[];
  violations: string[];
};

export type DocumentOut = {
  version: number;
  status: DocStatus;
  lines: DocLine[];
};

export type Counts = { covered: number; total: number };

export type CoverageItem = {
  id: string;
  kind: "must" | "nice";
  text: string;
  covered: boolean;
  line_ids: string[];
  decision: "have" | "lack" | null;
};

export type Coverage = {
  must: Counts;
  nice: Counts;
  items: CoverageItem[];
};

export type Requirement = { id: string; text: string };

export type Requirements = {
  must: Requirement[];
  nice: Requirement[];
  keywords: string[];
};

export type TaskItem = {
  kind: string;
  state: string;
  error: string | null;
  qid?: number;
};

export type ApplicationOut = {
  id: number;
  job_id: number;
  state: ApplicationState;
  error: string | null;
  locale: string;
  template: Template;
  referees: string | null;
  profile_version: number | null;
  applied_at: string | null;
};

export type ApplicationListItem = {
  id: number;
  job_id: number;
  job_title: string;
  company_name: string;
  state: ApplicationState;
  updated_at: string;
  applied_at: string | null;
  coverage: { must: Counts; nice: Counts } | null;
};

export type ApplicationDetail = {
  id: number;
  state: ApplicationState;
  error: string | null;
  locale: string;
  template: Template;
  referees: string | null;
  profile_version: number | null;
  current_profile_version: number | null;
  warnings: ("profile_changed" | "job_changed" | "job_closed")[];
  job: { id: number; title: string; company_name: string; url: string; status: string };
  requirements: Requirements | null;
  coverage: Coverage | null;
  resume: DocumentOut | null;
  cover_letter: DocumentOut | null;
  questions: unknown[];
  tasks: TaskItem[];
  can_generate: { allowed: boolean; reason: string | null };
  applied_at: string | null;
};

const JSON_HEADERS = { "Content-Type": "application/json" };

function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: body === undefined ? undefined : JSON_HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => r.body);
}

export function createApplication(jobId: number): Promise<ApplicationOut> {
  return send<ApplicationOut>("/applications", "POST", { job_id: jobId });
}

export function listApplications(): Promise<{ items: ApplicationListItem[] }> {
  return send("/applications", "GET");
}

export function getApplication(id: number): Promise<ApplicationDetail> {
  return send(`/applications/${id}`, "GET");
}

export function generateApplication(id: number): Promise<ApplicationOut> {
  return send(`/applications/${id}/generate`, "POST");
}

export function patchApplication(
  id: number,
  patch: { template?: Template; referees?: string },
): Promise<ApplicationOut> {
  return send(`/applications/${id}`, "PATCH", patch);
}

export function editLine(
  id: number,
  lineId: string,
  text: string,
  kind: DocKind = "resume",
): Promise<DocumentOut> {
  return send(`/applications/${id}/lines/${encodeURIComponent(lineId)}?kind=${kind}`, "PUT", {
    text,
  });
}

/** Cria a candidatura (ou abre a existente) e, só se ainda for rascunho, pede a geração. */
export async function prepareApplication(jobId: number): Promise<number> {
  const app = await createApplication(jobId);
  if (app.state === "draft") {
    await generateApplication(app.id);
  }
  return app.id;
}
