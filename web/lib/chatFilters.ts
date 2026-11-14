import {
  LANGUAGE_LABEL,
  SENIORITY_LABEL,
  type JobLanguage,
  type Seniority,
  type SearchFilters,
  type WorkModelFilter,
} from "@/lib/matching";
import { ApiError, request } from "@/lib/profile";

/** Resposta de POST /filters/parse: uma PROPOSTA, só com o que a frase diz. */
export type ParsedQuery = {
  filters: SearchFilters;
  query_text: string | null;
  notes: string[];
};

const MSG_PARSE_422 = "Não consegui transformar essa frase em filtros.";

export async function parseFilters(text: string): Promise<ParsedQuery> {
  try {
    const res = await request<ParsedQuery>("/filters/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    return res.body;
  } catch (err) {
    if (err instanceof ApiError && err.status === 422 && err.message.startsWith("A API respondeu com erro")) {
      throw new ApiError(422, MSG_PARSE_422);
    }
    throw err;
  }
}

/* Chips */

export type ChipField = keyof SearchFilters | "query_text";

export type Chip = {
  key: string;
  kind: "include" | "exclude" | "query" | "salary";
  field: ChipField;
  value: string;
  /** Texto puro (nunca HTML). */
  label: string;
};

const WORK_MODEL_LABEL: Record<WorkModelFilter, string> = {
  onsite: "presencial",
  hybrid: "híbrido",
  remote: "remoto",
};

type ListField =
  | "countries"
  | "work_models"
  | "seniorities"
  | "languages"
  | "departments"
  | "exclude_countries"
  | "exclude_work_models"
  | "exclude_seniorities"
  | "exclude_languages"
  | "exclude_departments"
  | "title_exclude";

const PAIRS = [
  ["countries", "exclude_countries"],
  ["work_models", "exclude_work_models"],
  ["seniorities", "exclude_seniorities"],
  ["languages", "exclude_languages"],
  ["departments", "exclude_departments"],
] as const satisfies readonly (readonly [ListField, ListField])[];

function valueLabel(field: ListField, value: string): string {
  const base = field.replace(/^exclude_/, "");
  if (base === "work_models") return WORK_MODEL_LABEL[value as WorkModelFilter] ?? value;
  if (base === "seniorities") return SENIORITY_LABEL[value as Seniority] ?? value;
  if (base === "languages") return LANGUAGE_LABEL[value as JobLanguage] ?? value;
  return value;
}

const amountFmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });

export function filterChips(f: SearchFilters, queryText: string | null): Chip[] {
  const chips: Chip[] = [];
  for (const [inc, exc] of PAIRS) {
    for (const v of f[inc] as string[]) {
      chips.push({ key: `${inc}:${v}`, kind: "include", field: inc, value: v, label: valueLabel(inc, v) });
    }
    for (const v of f[exc] as string[]) {
      chips.push({
        key: `${exc}:${v}`,
        kind: "exclude",
        field: exc,
        value: v,
        label: `sem ${valueLabel(exc, v)}`,
      });
    }
  }
  for (const v of f.title_exclude) {
    chips.push({ key: `title_exclude:${v}`, kind: "exclude", field: "title_exclude", value: v, label: `sem ${v}` });
  }
  if (f.salary_min !== null) {
    const cur = f.salary_currency ? `${f.salary_currency} ` : "";
    chips.push({
      key: "salary_min",
      kind: "salary",
      field: "salary_min",
      value: String(f.salary_min),
      label: `salário ≥ ${cur}${amountFmt.format(f.salary_min)}/ano`,
    });
  }
  if (queryText) {
    chips.push({ key: "query_text", kind: "query", field: "query_text", value: queryText, label: `gosto: ${queryText}` });
  }
  return chips;
}

export function removeChip(
  f: SearchFilters,
  queryText: string | null,
  chip: Chip,
): { filters: SearchFilters; queryText: string | null } {
  if (chip.field === "query_text") return { filters: f, queryText: null };
  if (chip.field === "salary_min") {
    return { filters: { ...f, salary_min: null, salary_currency: null }, queryText };
  }
  const field = chip.field as ListField;
  const list = f[field] as string[];
  return { filters: { ...f, [field]: list.filter((v) => v !== chip.value) }, queryText };
}

/* Fusão */

const MAX_LIST = 20;
const MAX_TITLE_EXCLUDE = 10;

const normFor = (field: ListField): ((v: string) => string) => {
  if (field.endsWith("departments") || field === "title_exclude") return (v) => v.trim().toLowerCase();
  if (field.endsWith("countries")) return (v) => v.trim().toUpperCase();
  return (v) => v;
};

function union(a: string[], b: string[], norm: (v: string) => string, max: number): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const v of [...a, ...b]) {
    const k = norm(v);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(v);
  }
  return out.slice(0, max);
}

const without = (list: string[], drop: string[], norm: (v: string) => string) => {
  const d = new Set(drop.map(norm));
  return list.filter((v) => !d.has(norm(v)));
};

/** Aplica a proposta da frase sobre os filtros atuais; a frase vence nos conflitos. */
export function mergeProposal(
  current: SearchFilters,
  currentQuery: string | null,
  p: ParsedQuery,
): { filters: SearchFilters; queryText: string | null } {
  const next: SearchFilters = { ...current };
  for (const [inc, exc] of PAIRS) {
    const norm = normFor(inc);
    const pInc = p.filters[inc] as string[];
    const pExc = p.filters[exc] as string[];
    const incAll = union(without(current[inc] as string[], pExc, norm), pInc, norm, Infinity);
    const excAll = union(without(current[exc] as string[], pInc, norm), pExc, norm, Infinity);
    // Rede de segurança: nunca devolve o mesmo valor nos dois lados.
    const excClean = without(excAll, incAll, norm);
    (next as Record<string, unknown>)[inc] = incAll.slice(0, MAX_LIST);
    (next as Record<string, unknown>)[exc] = excClean.slice(0, MAX_LIST);
  }
  next.title_exclude = union(
    current.title_exclude,
    p.filters.title_exclude,
    normFor("title_exclude"),
    MAX_TITLE_EXCLUDE,
  );
  if (p.filters.salary_min !== null) {
    next.salary_min = p.filters.salary_min;
    next.salary_currency = p.filters.salary_currency;
  }
  next.include_unknown = current.include_unknown;
  next.accept_no_salary = current.accept_no_salary;
  return { filters: next, queryText: p.query_text ?? currentQuery };
}
