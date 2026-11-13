import { ApiError, request } from "@/lib/profile";

/* Tipos espelhando api/discovery.py */

export type CandidateState = "pending" | "valid" | "invalid" | "known" | "rejected";

/** catalog | profile | target */
export type OriginKind = string;

export type DiscoveryState = {
  enabled: boolean;
  target_companies: string[];
  /** Empresas automáticas ativas. */
  auto_companies: number;
  /** Teto de empresas automáticas. */
  max_auto: number;
  /** Candidatos em `pending`, inclusive os parados atrás do teto. */
  pending: number;
  /** Candidatos em `valid`. */
  found: number;
};

export type DiscoveryInput = { enabled: boolean; target_companies: string[] };

export type Candidate = {
  token: string;
  name_hint: string | null;
  origin_kind: OriginKind;
  /** Um de CandidateState; string para tolerar estados novos. */
  state: string;
  attempts: number;
  last_error: string | null;
};

export type CandidatePage = { total: number; items: Candidate[] };

export type CandidateQuery = { state?: CandidateState | null; limit: number; offset: number };

/** Mesmos limites de rumo_core/discovery/candidates.py. */
export const MAX_TARGETS = 50;
export const MAX_TARGET_LEN = 80;
export const CANDIDATES_PAGE_SIZE = 50;

/* Cliente */

export async function getDiscovery(): Promise<DiscoveryState> {
  return (await request<DiscoveryState>("/discovery")).body;
}

const PUT_FALLBACK: Record<number, string> = {
  422: `Confira a lista: até ${MAX_TARGETS} nomes, cada um com até ${MAX_TARGET_LEN} caracteres e sem caracteres invisíveis.`,
};

export async function putDiscovery(input: DiscoveryInput): Promise<DiscoveryState> {
  try {
    return (
      await request<DiscoveryState>("/discovery", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      })
    ).body;
  } catch (err) {
    if (
      err instanceof ApiError &&
      err.status !== null &&
      err.status in PUT_FALLBACK &&
      err.message.startsWith("A API respondeu com erro")
    ) {
      throw new ApiError(err.status, PUT_FALLBACK[err.status]);
    }
    throw err;
  }
}

export async function listCandidates(query: CandidateQuery): Promise<CandidatePage> {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.state) params.set("state", query.state);
  return (await request<CandidatePage>(`/discovery/candidates?${params}`)).body;
}

/* Validação no navegador (o servidor valida de novo) */

export type ParsedTargets = { ok: true; names: string[] } | { ok: false; error: string };

/** Controle, formato/bidi e surrogates soltos, mais os separadores de linha/parágrafo. */
// Construtor em vez de literal: o tsconfig mira ES2017, que não conhece `\p{}` em literais.
const FORBIDDEN = new RegExp("[\\p{Cc}\\p{Cf}\\p{Cs}\\u2028\\u2029]", "u");

/** Comprimento em code points (como `len` no Python). */
const length = (s: string) => Array.from(s).length;

function preview(name: string): string {
  const chars = Array.from(name);
  return chars.length > 30 ? `${chars.slice(0, 30).join("")}…` : name;
}

/** Uma empresa por linha: sem linhas vazias, sem repetidos (maiúsculas e NFC), até 50 × 80. */
export function parseTargets(text: string): ParsedTargets {
  const names: string[] = [];
  const seen = new Set<string>();
  for (const line of text.split(/\r\n|\r|\n/)) {
    const name = line.normalize("NFC").trim();
    if (!name) continue;
    if (FORBIDDEN.test(name)) {
      return {
        ok: false,
        error: `“${preview(name)}” tem um caractere invisível ou de controle (como tabulação). Apague e digite o nome de novo.`,
      };
    }
    const len = length(name);
    if (len > MAX_TARGET_LEN) {
      return {
        ok: false,
        error: `“${preview(name)}” tem ${len} caracteres; o limite é ${MAX_TARGET_LEN}.`,
      };
    }
    const key = name.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    names.push(name);
  }
  if (names.length > MAX_TARGETS) {
    const extra = names.length - MAX_TARGETS;
    return {
      ok: false,
      error: `São ${names.length} empresas; o limite é ${MAX_TARGETS}. Remova ${extra}.`,
    };
  }
  return { ok: true, names };
}

/* Apresentação */

/** "12 empresas encontradas · 34 na fila" (a fila inclui o que espera o teto). */
export function discoveryCounters(s: Pick<DiscoveryState, "found" | "pending">): string {
  const found = s.found === 1 ? "1 empresa encontrada" : `${s.found} empresas encontradas`;
  return `${found} · ${s.pending} na fila`;
}

const STATE_LABEL: Record<CandidateState, string> = {
  pending: "na fila",
  valid: "adicionada",
  invalid: "sem vagas ou inexistente",
  known: "já existe",
  rejected: "ignorada",
};

export function candidateStateLabel(state: string): string {
  return Object.hasOwn(STATE_LABEL, state) ? STATE_LABEL[state as CandidateState] : "—";
}

const ORIGIN_LABEL: Record<string, string> = {
  catalog: "catálogo",
  profile: "currículo",
  target: "sua lista",
};

export function originKindLabel(kind: string): string {
  return Object.hasOwn(ORIGIN_LABEL, kind) ? ORIGIN_LABEL[kind] : "—";
}
