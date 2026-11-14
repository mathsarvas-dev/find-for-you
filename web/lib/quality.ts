import { request } from "@/lib/profile";

/* Tipos espelhando api/quality.py */

export type QualityLabel = "good" | "bad";

export type SampleItem = {
  job_id: number;
  title: string;
  company_name: string;
  country: string | null;
  url: string | null;
  snippet: string;
  label: QualityLabel | null;
};

export type QualitySample = { items: SampleItem[]; labeled_total: number };

export type LabelResult = { job_id: number; label: QualityLabel };

export type MisrankedItem = {
  job_id: number;
  title: string;
  label: QualityLabel;
  position: number;
};

export type QualityReport = {
  status: "insuficiente" | "dentro_da_meta" | "abaixo_da_meta";
  labeled: number;
  good: number;
  bad: number;
  unranked: number;
  missing: string[];
  precision_at_10: number | null;
  precision_vec_at_10: number | null;
  target: number;
  misranked: MisrankedItem[];
};

export async function getSample(): Promise<QualitySample> {
  return (await request<QualitySample>("/quality/sample")).body;
}

export async function newSample(): Promise<QualitySample> {
  return (await request<QualitySample>("/quality/sample/new", { method: "POST" })).body;
}

export async function putLabel(jobId: number, label: QualityLabel): Promise<LabelResult> {
  const res = await request<LabelResult>(`/quality/labels/${jobId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label }),
  });
  return res.body;
}

export async function deleteLabel(jobId: number): Promise<void> {
  await request<null>(`/quality/labels/${jobId}`, { method: "DELETE" });
}

export async function getReport(): Promise<QualityReport> {
  return (await request<QualityReport>("/quality/report")).body;
}
