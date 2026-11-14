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
    for (const v of dedupe(f[inc] as string[], normFor(inc))) {
      chips.push({ key: `${inc}:${v}`, kind: "include", field: inc, value: v, label: valueLabel(inc, v) });
    }
    for (const v of dedupe(f[exc] as string[], normFor(exc))) {
      chips.push({
        key: `${exc}:${v}`,
        kind: "exclude",
        field: exc,
        value: v,
        label: `sem ${valueLabel(exc, v)}`,
      });
    }
  }
  for (const v of dedupe(f.title_exclude, normFor("title_exclude"))) {
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
  // Usa a mesma igualdade da fusão (normFor), não a exata: remove todas as ocorrências equivalentes.
  const norm = normFor(field);
  const target = norm(chip.value);
  const list = f[field] as string[];
  return { filters: { ...f, [field]: list.filter((v) => norm(v) !== target) }, queryText };
}

/* Fusão */

const MAX_LIST = 20;
const MAX_TITLE_EXCLUDE = 10;

function normFor(field: ListField): (v: string) => string {
  if (field.endsWith("departments") || field === "title_exclude") return (v) => v.trim().toLowerCase();
  if (field.endsWith("countries")) return (v) => v.trim().toUpperCase();
  return (v) => v;
}

function dedupe(list: string[], norm: (v: string) => string): string[] {
  const seen = new Set<string>();
  return list.filter((v) => {
    const k = norm(v);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

const without = (list: string[], drop: string[], norm: (v: string) => string) => {
  const d = new Set(drop.map(norm));
  return list.filter((v) => !d.has(norm(v)));
};

/** Valores atuais (menos os que a frase contradiz ou repete) e depois os da frase. */
function combine(cur: string[], phrase: string[], drop: string[], norm: (v: string) => string): string[] {
  const p = dedupe(phrase, norm);
  return [...without(dedupe(cur, norm), [...drop, ...p], norm), ...p];
}

/** Sob o teto os valores da frase têm prioridade: descarta os mais antigos (início da lista). */
const cap = (list: string[], max: number) => (list.length > max ? list.slice(list.length - max) : list);

/** Aplica a proposta da frase sobre os filtros atuais; a frase vence nos conflitos. */
export function mergeProposal(
  current: SearchFilters,
  currentQuery: string | null,
  p: ParsedQuery,
): { filters: SearchFilters; queryText: string | null } {
  const next: SearchFilters = { ...current };
  for (const [inc, exc] of PAIRS) {
    const norm = normFor(inc);
    const pInc = dedupe(p.filters[inc] as string[], norm).slice(0, MAX_LIST);
    const pExc = dedupe(p.filters[exc] as string[], norm).slice(0, MAX_LIST);
    const incAll = combine(current[inc] as string[], pInc, pExc, norm);
    const excAll = combine(current[exc] as string[], pExc, pInc, norm);
    // Rede de segurança (antes do teto): nunca o mesmo valor nos dois lados.
    const excClean = without(excAll, incAll, norm);
    (next as Record<string, unknown>)[inc] = cap(incAll, MAX_LIST);
    (next as Record<string, unknown>)[exc] = cap(excClean, MAX_LIST);
  }
  {
    const norm = normFor("title_exclude");
    const pT = dedupe(p.filters.title_exclude, norm).slice(0, MAX_TITLE_EXCLUDE);
    next.title_exclude = cap(combine(current.title_exclude, pT, [], norm), MAX_TITLE_EXCLUDE);
  }
  if (p.filters.salary_min !== null) {
    next.salary_min = p.filters.salary_min;
    next.salary_currency = p.filters.salary_currency;
  }
  next.include_unknown = current.include_unknown;
  next.accept_no_salary = current.accept_no_salary;
  return { filters: next, queryText: p.query_text ?? currentQuery };
}
