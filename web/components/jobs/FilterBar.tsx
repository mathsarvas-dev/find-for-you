"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  LANGUAGE_LABEL,
  SENIORITY_LABEL,
  type JobLanguage,
  type SearchFilters,
  type Seniority,
  type WorkModelFilter,
} from "@/lib/matching";

export const FILTER_DEBOUNCE_MS = 300;
/** Mesmo limite da API (listas de até 20 itens). */
const MAX_ITEMS = 20;

const WORK_MODELS: [WorkModelFilter, string][] = [
  ["remote", "remoto"],
  ["hybrid", "híbrido"],
  ["onsite", "presencial"],
];
const SENIORITIES = Object.entries(SENIORITY_LABEL) as [Seniority, string][];
const LANGUAGES = Object.entries(LANGUAGE_LABEL) as [JobLanguage, string][];

type Draft = {
  countries: string;
  departments: string;
  work_models: WorkModelFilter[];
  seniorities: Seniority[];
  languages: JobLanguage[];
  salary: string;
  currency: string;
  include_unknown: boolean;
  accept_no_salary: boolean;
};

function toDraft(f: SearchFilters): Draft {
  return {
    countries: f.countries.join(", "),
    departments: f.departments.join(", "),
    work_models: f.work_models,
    seniorities: f.seniorities,
    languages: f.languages,
    salary: f.salary_min === null ? "" : String(f.salary_min),
    currency: f.salary_currency ?? "",
    include_unknown: f.include_unknown,
    accept_no_salary: f.accept_no_salary,
  };
}

function splitList(raw: string, upperCodes = false): string[] {
  const out: string[] = [];
  for (const part of raw.split(",")) {
    let v = part.trim();
    if (!v) continue;
    if (upperCodes && v.length === 2) v = v.toUpperCase();
    if (!out.includes(v)) out.push(v);
  }
  return out.slice(0, MAX_ITEMS);
}

type Parsed = { filters: SearchFilters; error: null } | { filters: null; error: Problem };
type Problem = { field: "salary" | "currency"; message: string };

function parse(d: Draft): Parsed {
  const currency = d.currency.trim().toUpperCase() || null;
  let salary: number | null = null;
  if (d.salary.trim() !== "") {
    salary = Number(d.salary);
    if (!Number.isFinite(salary) || salary < 0) {
      return { filters: null, error: { field: "salary", message: "Salário mínimo precisa ser um número positivo." } };
    }
    if (!currency) {
      return {
        filters: null,
        error: { field: "currency", message: "Informe a moeda do salário mínimo (ex.: BRL)." },
      };
    }
  }
  return {
    error: null,
    filters: {
      countries: splitList(d.countries, true),
      work_models: d.work_models,
      seniorities: d.seniorities,
      languages: d.languages,
      departments: splitList(d.departments),
      salary_min: salary,
      salary_currency: currency,
      include_unknown: d.include_unknown,
      accept_no_salary: d.accept_no_salary,
    },
  };
}

function toggle<T>(list: T[], value: T, on: boolean): T[] {
  return on ? (list.includes(value) ? list : [...list, value]) : list.filter((v) => v !== value);
}

type FilterBarProps = {
  /** Filtros salvos; lidos só ao montar. */
  initial: SearchFilters;
  /** Chamado 300 ms depois da última mudança, só com filtros válidos. */
  onChange: (filters: SearchFilters) => void;
  /** Texto curto de estado ("salvando…"). */
  status?: string;
  /** Erro ao salvar, vindo da página. */
  error?: string | null;
};

/** Filtros manuais. Cada mudança, depois do debounce, vira um PUT do perfil de busca (na página). */
export function FilterBar({ initial, onChange, status, error }: FilterBarProps) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(initial));
  const [dirty, setDirty] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const onChangeRef = useRef(onChange);
  const id = useId();

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    if (!dirty) return;
    const timer = setTimeout(() => {
      const parsed = parse(draft);
      setProblem(parsed.error);
      if (parsed.filters) onChangeRef.current(parsed.filters);
    }, FILTER_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, dirty]);

  function update(patch: Partial<Draft>) {
    setDraft((d) => ({ ...d, ...patch }));
    setDirty(true);
  }

  const salaryErr = problem?.field === "salary" ? problem.message : null;
  const currencyErr = problem?.field === "currency" ? problem.message : null;

  return (
    <section className="panel filters" aria-labelledby={`${id}-title`}>
      <div className="panel__head">
        <h2 id={`${id}-title`}>Filtros</h2>
        <span className="mono" aria-live="polite">
          {status ?? ""}
        </span>
      </div>

      <div className="filters__fields">
        <div className="field">
          <label className="field__label" htmlFor={`${id}-countries`}>
            Países
          </label>
          <input
            id={`${id}-countries`}
            className="input"
            type="text"
            autoComplete="off"
            placeholder="BR, PT, DE"
            value={draft.countries}
            onChange={(e) => update({ countries: e.target.value })}
          />
        </div>
        <div className="field">
          <label className="field__label" htmlFor={`${id}-departments`}>
            Departamentos
          </label>
          <input
            id={`${id}-departments`}
            className="input"
            type="text"
            autoComplete="off"
            placeholder="Engineering, Data"
            value={draft.departments}
            onChange={(e) => update({ departments: e.target.value })}
          />
        </div>
        <div className="filters__salary">
          <div className="field">
            <label className="field__label" htmlFor={`${id}-salary`}>
              Salário mínimo por ano
            </label>
            <input
              id={`${id}-salary`}
              className="input mono"
              type="number"
              inputMode="numeric"
              min={0}
              step={1000}
              placeholder="120000"
              value={draft.salary}
              aria-invalid={salaryErr ? true : undefined}
              aria-describedby={salaryErr ? `${id}-salary-err` : undefined}
              onChange={(e) => update({ salary: e.target.value })}
            />
          </div>
          <div className="field filters__currency">
            <label className="field__label" htmlFor={`${id}-currency`}>
              Moeda
            </label>
            <input
              id={`${id}-currency`}
              className="input mono"
              type="text"
              autoComplete="off"
              maxLength={8}
              placeholder="BRL"
              value={draft.currency}
              aria-invalid={currencyErr ? true : undefined}
              aria-describedby={currencyErr ? `${id}-salary-err` : undefined}
              onChange={(e) => update({ currency: e.target.value })}
            />
          </div>
          {(salaryErr || currencyErr) && (
            <p id={`${id}-salary-err`} className="field__error filters__error">
              {salaryErr ?? currencyErr}
            </p>
          )}
        </div>
      </div>

      <div className="filters__groups">
        <OptionGroup
          legend="Modelo"
          options={WORK_MODELS}
          selected={draft.work_models}
          onToggle={(v, on) => update({ work_models: toggle(draft.work_models, v, on) })}
        />
        <OptionGroup
          legend="Idioma da vaga"
          options={LANGUAGES}
          selected={draft.languages}
          onToggle={(v, on) => update({ languages: toggle(draft.languages, v, on) })}
        />
        <OptionGroup
          legend="Senioridade"
          options={SENIORITIES}
          selected={draft.seniorities}
          onToggle={(v, on) => update({ seniorities: toggle(draft.seniorities, v, on) })}
        />
      </div>

      <div className="filters__switches">
        <label className="check">
          <input
            type="checkbox"
            checked={draft.accept_no_salary}
            onChange={(e) => update({ accept_no_salary: e.target.checked })}
          />
          <span>Aceitar vagas sem salário</span>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={draft.include_unknown}
            onChange={(e) => update({ include_unknown: e.target.checked })}
          />
          <span>
            Incluir desconhecidos{" "}
            <span className="muted">(vagas em que a fonte não diz país, modelo, senioridade ou idioma)</span>
          </span>
        </label>
      </div>

      {error && (
        <div className="notice" role="alert">
          <strong>Os filtros não foram salvos.</strong>
          <p>{error}</p>
        </div>
      )}
    </section>
  );
}

function OptionGroup<T extends string>({
  legend,
  options,
  selected,
  onToggle,
}: {
  legend: string;
  options: [T, string][];
  selected: T[];
  onToggle: (value: T, on: boolean) => void;
}) {
  return (
    <fieldset className="options">
      <legend className="field__label">{legend}</legend>
      <div className="options__list">
        {options.map(([value, label]) => (
          <label key={value} className="option">
            <input
              type="checkbox"
              checked={selected.includes(value)}
              onChange={(e) => onToggle(value, e.target.checked)}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
