import type { MatchDetail, MatchItem, MatchJob, MatchPage } from "@/lib/matching";

/** Dados de teste compartilhados pelos testes da tela de vagas. */

export function matchJob(id: number, extra: Partial<MatchJob> = {}): MatchJob {
  return {
    id,
    company_id: 1,
    company_name: "GitLab",
    title: `Vaga ${id}`,
    url: `https://boards.greenhouse.io/gitlab/jobs/${id}`,
    location_text: "Remote, Brazil",
    country: "BR",
    city: null,
    work_model: "remote",
    seniority: "senior",
    language: "en",
    department: "Data",
    salary_known: false,
    status: "open",
    first_seen_at: "2026-11-12T11:00:00Z",
    posted_at: "2026-11-10T12:00:00Z",
    salary_min: null,
    salary_max: null,
    salary_currency: null,
    salary_period: null,
    ...extra,
  };
}

export function matchItem(
  id: number,
  extra: Partial<Omit<MatchItem, "job">> = {},
  job: Partial<MatchJob> = {},
): MatchItem {
  return {
    job: matchJob(id, job),
    vec_score: 0.8 - id / 100,
    ai_score: null,
    state: null,
    unknown_fields: ["salary"],
    ...extra,
  };
}

export function matchPage(items: MatchItem[], extra: Partial<MatchPage> = {}): MatchPage {
  return {
    total: items.length,
    profile_version: 3,
    vectorized: true,
    missing_embeddings: 0,
    chunks_used: 3,
    query_used: false,
    items,
    ...extra,
  };
}

export function matchDetail(id: number, extra: Partial<MatchDetail> = {}): MatchDetail {
  const { salary_min, salary_max, salary_currency, salary_period, ...job } = matchJob(id);
  return {
    ...job,
    description: "Descrição da vaga.",
    salary_min,
    salary_max,
    salary_currency,
    salary_period,
    last_seen_at: "2026-11-12T11:00:00Z",
    closed_at: null,
    vec_score: 0.79,
    ai_score: 84,
    state: "ok",
    rationale: "O perfil tem cinco anos com Python e Airflow, que a vaga pede.",
    strengths: ["Python em produção", "Airflow"],
    gaps: ["Sem experiência com Spark citada no perfil"],
    error: null,
    unknown_fields: ["salary"],
    ...extra,
  };
}
