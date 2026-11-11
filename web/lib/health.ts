import type { StatusState } from "@/components/StatusRow";

export const DEFAULT_API_URL = "http://localhost:8000";
const TIMEOUT_MS = 3000;

export type HealthProblem = "api-unreachable" | "api-unexpected" | "db-down";

export type HealthReport = {
  apiUrl: string;
  api: StatusState;
  db: StatusState;
  apiDetail: string;
  dbDetail: string;
  problem: HealthProblem | null;
  httpStatus: number | null;
  checkedAt: Date;
};

export function apiBaseUrl(): string {
  return (process.env.API_URL || DEFAULT_API_URL).replace(/\/+$/, "");
}

type Outcome =
  | { kind: "response"; status: number; body: unknown; ms: number }
  | { kind: "no-response"; reason: "timeout" | "network" };

/** Traduz a resposta de GET /health nos estados da página. Sem I/O, para testar. */
export function interpretHealth(apiUrl: string, outcome: Outcome, checkedAt: Date): HealthReport {
  const base = { apiUrl, checkedAt };
  if (outcome.kind === "no-response") {
    return {
      ...base,
      api: "down",
      db: "unknown",
      apiDetail:
        outcome.reason === "timeout" ? `sem resposta em ${TIMEOUT_MS / 1000} s` : "sem conexão",
      dbDetail: "depende da API",
      problem: "api-unreachable",
      httpStatus: null,
    };
  }

  const { status, body, ms } = outcome;
  const db = (body as { db?: unknown } | null)?.db;
  const apiDetail = `GET /health · ${status} · ${ms} ms`;

  if (status === 200 && db === "up") {
    return { ...base, api: "ok", db: "ok", apiDetail, dbDetail: "SELECT 1 ok", problem: null, httpStatus: status };
  }
  if (status === 503 && db === "down") {
    return {
      ...base,
      api: "ok",
      db: "down",
      apiDetail,
      dbDetail: "a API não conectou",
      problem: "db-down",
      httpStatus: status,
    };
  }
  return {
    ...base,
    api: "down",
    db: "unknown",
    apiDetail,
    dbDetail: "depende da API",
    problem: "api-unexpected",
    httpStatus: status,
  };
}

export async function checkHealth(): Promise<HealthReport> {
  const apiUrl = apiBaseUrl();
  const started = Date.now();
  let outcome: Outcome;
  try {
    const res = await fetch(`${apiUrl}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const body = await res.json().catch(() => null);
    outcome = { kind: "response", status: res.status, body, ms: Date.now() - started };
  } catch (err) {
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    outcome = { kind: "no-response", reason: timedOut ? "timeout" : "network" };
  }
  return interpretHealth(apiUrl, outcome, new Date());
}
