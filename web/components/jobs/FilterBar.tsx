"use client";

import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import {
  LANGUAGE_LABEL,
  SENIORITY_LABEL,
  type JobLanguage,
  type SearchFilters,
  type Seniority,
  type WorkModelFilter,
} from "@/lib/matching";

export const FILTER_DEBOUNCE_MS = 300;
/** Mesmos limites da API: listas de até 20 itens; até 10 termos a evitar no título. */
const MAX_ITEMS = 20;
const MAX_TITLE_TERMS = 10;

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
  /** Exclusões editáveis aqui (texto separado por vírgula). */
  exclude_countries: string;
  title_exclude: string;
  /** Exclusões que só mudam por chip ou frase: o painel as preserva intactas. */
  exclusions: Exclusions;
};

type Exclusions = Pick<
  SearchFilters,
  "exclude_work_models" | "exclude_seniorities" | "exclude_languages" | "exclude_departments"
>;

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
    exclude_countries: f.exclude_countries.join(", "),
    title_exclude: f.title_exclude.join(", "),
    exclusions: {
      exclude_work_models: f.exclude_work_models,
      exclude_seniorities: f.exclude_seniorities,
      exclude_languages: f.exclude_languages,
      exclude_departments: f.exclude_departments,
    },
  };
}

const same = (v: string) => v;
const lower = (v: string) => v.toLowerCase();
const upper = (v: string) => v.toUpperCase();

/** Lista separada por vírgula, sem repetidos (pela chave `norm`), até `max` itens. */
function splitList(raw: string, { upperCodes = false, norm = same, max = MAX_ITEMS } = {}): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const part of raw.split(",")) {
    let v = part.trim();
    if (!v) continue;
    if (upperCodes && v.length === 2) v = v.toUpperCase();
    const k = norm(v);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(v);
  }
  return out.slice(0, max);
}

/** Tira da exclusão o que também está na inclusão (a inclusão vence). */
function minus<T extends string>(exclude: T[], include: T[], norm: (v: string) => string = same): T[] {
  const inc = new Set(include.map(norm));
  return exclude.filter((v) => !inc.has(norm(v)));
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
  const countries = splitList(d.countries, { upperCodes: true });
  const departments = splitList(d.departments);
  const ex = d.exclusions;
  return {
    error: null,
    filters: {
      countries,
      work_models: d.work_models,
      seniorities: d.seniorities,
      languages: d.languages,
      departments,
      exclude_countries: minus(splitList(d.exclude_countries, { upperCodes: true }), countries, upper),
      exclude_work_models: minus(ex.exclude_work_models, d.work_models),
      exclude_seniorities: minus(ex.exclude_seniorities, d.seniorities),
      exclude_languages: minus(ex.exclude_languages, d.languages),
      exclude_departments: minus(ex.exclude_departments, departments, lower),
      title_exclude: splitList(d.title_exclude, { norm: lower, max: MAX_TITLE_TERMS }),
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

/** Até 560 px o painel começa recolhido. */
const NARROW_QUERY = "(max-width: 560px)";

function subscribeNarrow(onChange: () => void) {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
  const mq = window.matchMedia(NARROW_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

const isNarrow = () =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia(NARROW_QUERY).matches;

/** Pré-render e hidratação assumem tela larga (aberto); o cliente corrige depois. */
const serverNarrow = () => false;

function activeCount(d: Draft): number {
  return [
    d.countries.trim() !== "",
    d.departments.trim() !== "",
    d.work_models.length > 0,
    d.seniorities.length > 0,
    d.languages.length > 0,
    d.salary.trim() !== "",
    d.exclude_countries.trim() !== "",
    d.title_exclude.trim() !== "",
    ...Object.values(d.exclusions).map((list) => list.length > 0),
  ].filter(Boolean).length;
}

const activeText = (n: number) =>
  n === 0 ? "nenhum ativo" : `${n} ${n === 1 ? "ativo" : "ativos"}`;

type FilterBarProps = {
  /** Filtros salvos; lidos ao montar e quando `revision` muda. */
  initial: SearchFilters;
  /**
   * Muda quando os filtros salvos mudam por fora do painel (chat, chips): o painel redesenha
   * a partir de `initial` e descarta o rascunho, para não salvar exclusões velhas depois.
   */
  revision: number;
  /** Chamado 300 ms depois da última mudança, só com filtros válidos. */
  onChange: (filters: SearchFilters) => void;
  /** Texto curto de estado ("salvando…"). */
  status?: string;
  /** Erro ao salvar, vindo da página. */
  error?: string | null;
  /** true assim que há uma edição esperando o debounce; false quando ele dispara. */
  onEditing?: (pending: boolean) => void;
};

/** Filtros manuais. Cada mudança, depois do debounce, vira um PUT do perfil de busca (na página). */
export function FilterBar({ initial, revision, onChange, status, error, onEditing }: FilterBarProps) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(initial));
  /** true: há edição da pessoa esperando o debounce. */
  const [dirty, setDirty] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [seenRevision, setSeenRevision] = useState(revision);
  if (revision !== seenRevision) {
    // Ajuste durante o render (padrão do React para estado derivado de prop).
    setSeenRevision(revision);
    setDraft(toDraft(initial));
    setDirty(false);
    setProblem(null);
  }
  /** null: segue a largura da tela; depois do primeiro clique, vale a escolha da pessoa. */
  const [chosenOpen, setChosenOpen] = useState<boolean | null>(null);
  const narrow = useSyncExternalStore(subscribeNarrow, isNarrow, serverNarrow);
  const open = chosenOpen ?? !narrow;
  const onChangeRef = useRef(onChange);
  const onEditingRef = useRef(onEditing);
  const id = useId();

  useEffect(() => {
    onChangeRef.current = onChange;
    onEditingRef.current = onEditing;
  }, [onChange, onEditing]);

  useEffect(() => {
    if (!dirty) return;
    const timer = setTimeout(() => {
      const parsed = parse(draft);
      onEditingRef.current?.(false);
      setProblem(parsed.error);
      if (!parsed.filters) return;
      setDirty(false);
      // O campo mostra o que foi salvo (sem o país que passou a ser incluído).
      const shown = parsed.filters.exclude_countries.join(", ");
      if (splitList(draft.exclude_countries, { upperCodes: true }).join(", ") !== shown) {
        setDraft((d) => ({ ...d, exclude_countries: shown }));
      }
      onChangeRef.current(parsed.filters);
    }, FILTER_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, dirty]);

  /** Marcar uma inclusão tira o mesmo valor da exclusão correspondente. */
  function include(field: "work_models" | "seniorities" | "languages", value: string, on: boolean) {
    const exKey = `exclude_${field}` as const;
    const list = draft[field] as string[];
    const patch: Record<string, unknown> = { [field]: toggle(list, value, on) };
    if (on) {
      const excluded = draft.exclusions[exKey] as string[];
      patch.exclusions = { ...draft.exclusions, [exKey]: excluded.filter((v) => v !== value) };
    }
    update(patch as Partial<Draft>);
  }

  function update(patch: Partial<Draft>) {
    setDraft((d) => ({ ...d, ...patch }));
    setDirty(true);
    onEditingRef.current?.(true);
  }

  const salaryErr = problem?.field === "salary" ? problem.message : null;
  const currencyErr = problem?.field === "currency" ? problem.message : null;

  return (
    <section className="panel filters" aria-labelledby={`${id}-title`}>
      <div className="panel__head filters__head">
        <h2 id={`${id}-title`}>
          <button
            type="button"
            className="filters__toggle"
            aria-expanded={open}
            aria-controls={`${id}-body`}
            onClick={() => setChosenOpen(!open)}
          >
            <span className="filters__chevron" aria-hidden="true" />
            Filtros <span className="filters__count">· {activeText(activeCount(draft))}</span>
          </button>
        </h2>
        <span className="mono" aria-live="polite">
          {status ?? ""}
        </span>
      </div>

      <div id={`${id}-body`} className="filters__body" hidden={!open}>
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
              <p id={`${id}-salary-err`} className="field__error filters__error" role="alert">
                {salaryErr ?? currencyErr}
              </p>
            )}
          </div>
        </div>
        <div className="filters__fields filters__fields--exclude">
          <div className="field">
            <label className="field__label" htmlFor={`${id}-exclude-countries`}>
              Excluir países
            </label>
            <input
              id={`${id}-exclude-countries`}
              className="input"
              type="text"
              autoComplete="off"
              placeholder="US, CN"
              value={draft.exclude_countries}
              onChange={(e) => update({ exclude_countries: e.target.value })}
            />
          </div>
          <div className="field">
            <label className="field__label" htmlFor={`${id}-title-exclude`}>
              Evitar no título
            </label>
            <input
              id={`${id}-title-exclude`}
              className="input"
              type="text"
              autoComplete="off"
              placeholder="plantão, estágio"
              value={draft.title_exclude}
              onChange={(e) => update({ title_exclude: e.target.value })}
            />
          </div>
        </div>

        <div className="filters__groups">
          <OptionGroup
            legend="Modelo"
            options={WORK_MODELS}
            selected={draft.work_models}
            onToggle={(v, on) => include("work_models", v, on)}
          />
          <OptionGroup
            legend="Idioma da vaga"
            options={LANGUAGES}
            selected={draft.languages}
            onToggle={(v, on) => include("languages", v, on)}
          />
          <OptionGroup
            legend="Senioridade"
            options={SENIORITIES}
            selected={draft.seniorities}
            onToggle={(v, on) => include("seniorities", v, on)}
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
      </div>

      {error && (
        <div className="notice notice--top" role="alert">
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
