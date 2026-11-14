import type { QualityReport, SampleItem } from "@/lib/quality";

/** Dados de teste compartilhados pelos testes da tela de qualidade. */
export function sampleItem(extra: Partial<SampleItem> = {}): SampleItem {
  return {
    job_id: 1,
    title: "Engenheira de dados",
    company_name: "Acme",
    country: "BR",
    url: "https://acme.example/jobs/1",
    snippet: "Pipelines em Python.",
    label: null,
    ...extra,
  };
}

export function qualityReport(extra: Partial<QualityReport> = {}): QualityReport {
  return {
    status: "dentro_da_meta",
    labeled: 40,
    good: 22,
    bad: 18,
    unranked: 0,
    missing: [],
    precision_at_10: 0.6,
    precision_vec_at_10: 0.5,
    target: 0.6,
    misranked: [],
    ...extra,
  };
}
